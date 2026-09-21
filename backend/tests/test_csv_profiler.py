from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.services.csv_profiler import ProfileError, infer_sample_type, profile_csv


def test_infer_sample_type_is_conservative_for_identifiers() -> None:
    result = infer_sample_type("postal_code", ["00123", "90210"])

    assert result["candidate_type"] == "VARCHAR"
    assert "identifier-like" in result["reason"]


@pytest.mark.parametrize(
    ("name", "values", "expected"),
    [
        ("has_cms", ["true", "false"], "BOOLEAN"),
        ("product_images", ["0", "1", "0"], "BIGINT"),
        ("domain_count", ["1", "2", "4"], "BIGINT"),
        ("visits", ["1", "250", "-3"], "BIGINT"),
        ("rating", ["4.2", "3", "1.25"], "DOUBLE"),
        ("created", ["2026/01/02", "2025-12-31"], "DATE"),
    ],
)
def test_infer_sample_types(name: str, values: list[str], expected: str) -> None:
    assert infer_sample_type(name, values)["candidate_type"] == expected


def test_money_stays_raw_with_derived_types() -> None:
    result = infer_sample_type(
        "estimated_monthly_sales", ["USD $51,707,048.53", "EUR 100.00"]
    )

    assert result["candidate_type"] == "VARCHAR"
    assert result["derived_types"]["amount"] == "DECIMAL(20, 2)"


def test_profile_csv_end_to_end_and_does_not_modify_source(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text(
        "domain,visits,created,technologies,homepage,installed_apps,sales,currency\n"
        "one.example,10,2026/01/01,Shopify:Klaviyo,https://one.example,"
        'https://apps.example/one/:https://apps.example/two/,"USD $1,234.50",USD\n'
        "two.example,20,not-a-date,WooCommerce,https://two.example:8443,"
        "https://apps.example/one/,EUR 20.00,EUR\n"
        "two.example,,2026-02-03,,,,GBP 0.99,GBP\n",
        encoding="utf-8",
    )
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    original_stat = source.stat()

    json_path, markdown_path, profile = profile_csv(
        source,
        tmp_path / "reports",
        memory_limit="256MB",
        sample_rows=100,
    )

    after = hashlib.sha256(source.read_bytes()).hexdigest()
    assert before == after
    assert source.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert json_path.exists()
    assert markdown_path.exists()
    assert profile["parser"]["accepted_row_count"] == 3
    assert profile["parser"]["column_count"] == 8
    assert profile["domain"]["duplicate_row_count"] == 1
    by_name = {column["name"]: column for column in profile["columns"]}
    assert by_name["created"]["cast_validation"]["failed_count"] == 1
    assert (
        by_name["technologies"]["delimiter_behavior"]["classification"]
        == "likely_collection"
    )
    assert (
        by_name["homepage"]["delimiter_behavior"]["classification"]
        == "likely_scalar"
    )
    installed_apps = by_name["installed_apps"]["delimiter_behavior"]
    assert installed_apps["classification"] == "likely_collection"
    assert installed_apps["delimiter_rule"] == r":(?=https?://)"
    money = by_name["sales"]["derived_validation"]
    assert money["failed_count"] == 0
    assert {item["currency"] for item in money["currencies"]} == {
        "EUR",
        "GBP",
        "USD",
    }


def test_rejects_source_as_output_path(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("value\n1\n", encoding="utf-8")

    with pytest.raises(ProfileError):
        profile_csv(source, source)
