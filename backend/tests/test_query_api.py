from __future__ import annotations

from pathlib import Path
from uuid import UUID

import duckdb
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    database = tmp_path / "storeleads.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            """
            CREATE TABLE stores (
                store_id UUID PRIMARY KEY,
                domain VARCHAR,
                title VARCHAR,
                country_code VARCHAR,
                status VARCHAR,
                estimated_monthly_visits BIGINT,
                rank BIGINT,
                created DATE,
                has_cms BOOLEAN,
                technologies VARCHAR
            )
            """
        )
        connection.executemany(
            "INSERT INTO stores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    UUID(int=1),
                    "alpha.example",
                    "Alpha",
                    "US",
                    "active",
                    100,
                    1,
                    "2026-01-01",
                    True,
                    "WooCommerce:Klaviyo",
                ),
                (
                    UUID(int=2),
                    "beta.example",
                    None,
                    "CA",
                    "active",
                    100,
                    2,
                    "2026-01-02",
                    False,
                    "WooCommerce",
                ),
                (
                    UUID(int=3),
                    "gamma.example",
                    "Gamma",
                    None,
                    "inactive",
                    50,
                    3,
                    None,
                    None,
                    "Klaviyo",
                ),
            ],
        )
        connection.execute(
            "CREATE TABLE store_technologies "
            "(store_id UUID, ordinal INTEGER, value VARCHAR)"
        )
        connection.executemany(
            "INSERT INTO store_technologies VALUES (?, ?, ?)",
            [
                (UUID(int=1), 1, "WooCommerce"),
                (UUID(int=1), 2, "Klaviyo"),
                (UUID(int=2), 1, "WooCommerce"),
                (UUID(int=3), 1, "Klaviyo"),
            ],
        )
    with TestClient(create_app(database_path=database)) as test_client:
        yield test_client


def test_schema_is_complete_and_marks_collections(client: TestClient) -> None:
    response = client.get("/api/schema")

    assert response.status_code == 200
    body = response.json()
    columns = {item["name"]: item for item in body["columns"]}
    assert body["schema_version"] == 1
    assert len(columns) == 150
    assert "notes" not in columns
    assert columns["estimated_monthly_visits"]["data_type"] == "integer"
    assert columns["technologies"]["filter_operators"] == [
        "has",
        "has_any",
        "has_all",
        "is_null",
        "is_not_null",
    ]


def test_nested_filters_projection_and_collection_membership(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/query",
        json={
            "columns": ["domain", "title"],
            "filters": [
                {
                    "combinator": "or",
                    "filters": [
                        {"column": "country_code", "operator": "eq", "value": "CA"},
                        {
                            "column": "technologies",
                            "operator": "has_all",
                            "value": ["WooCommerce", "Klaviyo"],
                        },
                    ],
                }
            ],
            "sort": [{"column": "rank", "direction": "desc"}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "rows": [
            {"domain": "beta.example", "title": None},
            {"domain": "alpha.example", "title": "Alpha"},
        ],
        "next_cursor": None,
    }


def test_cursor_pagination_is_stable_with_ties_and_hidden_sort(
    client: TestClient,
) -> None:
    payload = {
        "columns": ["domain"],
        "sort": [{"column": "estimated_monthly_visits", "direction": "desc"}],
        "limit": 1,
    }
    first = client.post("/api/query", json=payload)
    assert first.status_code == 200
    assert first.json()["rows"] == [{"domain": "alpha.example"}]

    payload["cursor"] = first.json()["next_cursor"]
    second = client.post("/api/query", json=payload)
    payload["cursor"] = second.json()["next_cursor"]
    third = client.post("/api/query", json=payload)

    assert second.json()["rows"] == [{"domain": "beta.example"}]
    assert third.json() == {
        "rows": [{"domain": "gamma.example"}],
        "next_cursor": None,
    }


def test_null_semantics_are_explicit(client: TestClient) -> None:
    not_equal = client.post(
        "/api/query",
        json={
            "columns": ["domain"],
            "filters": [
                {"column": "country_code", "operator": "neq", "value": "US"}
            ],
        },
    )
    is_null = client.post(
        "/api/query",
        json={
            "columns": ["domain"],
            "filters": [{"column": "country_code", "operator": "is_null"}],
        },
    )

    assert not_equal.json()["rows"] == [{"domain": "beta.example"}]
    assert is_null.json()["rows"] == [{"domain": "gamma.example"}]


def test_title_token_search_uses_word_boundaries(client: TestClient) -> None:
    match = client.post(
        "/api/query",
        json={
            "columns": ["domain"],
            "filters": [
                {"column": "title", "operator": "matches_token", "value": "alpha"}
            ],
        },
    )
    partial = client.post(
        "/api/query",
        json={
            "columns": ["domain"],
            "filters": [
                {"column": "title", "operator": "matches_token", "value": "alp"}
            ],
        },
    )

    assert match.json()["rows"] == [{"domain": "alpha.example"}]
    assert partial.json()["rows"] == []


def test_identifiers_are_allowlisted_and_values_are_bound(client: TestClient) -> None:
    invalid_column = client.post(
        "/api/query",
        json={
            "filters": [
                {"column": "domain; DROP TABLE stores", "operator": "eq", "value": "x"}
            ]
        },
    )
    invalid_operator = client.post(
        "/api/query",
        json={
            "filters": [
                {"column": "domain", "operator": "= ?; DROP TABLE stores", "value": "x"}
            ]
        },
    )
    injected_value = client.post(
        "/api/query",
        json={
            "columns": ["domain"],
            "filters": [
                {"column": "domain", "operator": "eq", "value": "x' OR TRUE --"}
            ],
        },
    )
    wildcard_value = client.post(
        "/api/query",
        json={
            "columns": ["domain"],
            "filters": [{"column": "domain", "operator": "contains", "value": "%"}],
        },
    )
    after = client.post("/api/query", json={"columns": ["domain"]})

    assert invalid_column.status_code == 422
    assert invalid_column.json()["error"]["code"] == "invalid_column"
    assert invalid_operator.status_code == 422
    assert invalid_operator.json()["error"]["code"] == "invalid_operator"
    assert injected_value.json()["rows"] == []
    assert wildcard_value.status_code == 200
    assert wildcard_value.json()["rows"] == []
    assert len(after.json()["rows"]) == 3


def test_invalid_types_cursors_and_request_shapes_are_structured(
    client: TestClient,
) -> None:
    wrong_type = client.post(
        "/api/query",
        json={
            "filters": [
                {
                    "column": "estimated_monthly_visits",
                    "operator": "gte",
                    "value": "a lot",
                }
            ]
        },
    )
    bad_cursor = client.post("/api/query", json={"cursor": "not-a-cursor"})
    extra_field = client.post("/api/query", json={"sql": "SELECT * FROM stores"})

    assert wrong_type.status_code == 422
    assert wrong_type.json()["error"]["code"] == "invalid_filter_value"
    assert bad_cursor.status_code == 422
    assert bad_cursor.json()["error"]["code"] == "invalid_cursor"
    assert extra_field.status_code == 422
    assert extra_field.json()["error"]["code"] == "request_validation_error"


def test_facets_respect_filters_and_collection_values(client: TestClient) -> None:
    scalar = client.post(
        "/api/facets",
        json={
            "column": "country_code",
            "filters": [{"column": "status", "operator": "eq", "value": "active"}],
        },
    )
    collection = client.post(
        "/api/facets", json={"column": "technologies", "limit": 10}
    )

    assert scalar.json()["values"] == [
        {"value": "CA", "count": 1},
        {"value": "US", "count": 1},
    ]
    assert collection.json()["values"] == [
        {"value": "Klaviyo", "count": 2},
        {"value": "WooCommerce", "count": 2},
    ]


def test_missing_database_is_a_structured_service_error(tmp_path: Path) -> None:
    with TestClient(create_app(database_path=tmp_path / "missing.duckdb")) as client:
        response = client.post("/api/query", json={})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
