import {
  flexRender,
  getCoreRowModel,
  type ColumnDef,
  type ColumnOrderState,
  type ColumnSizingState,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import { cancelExport, createExport, getExport, getFacet, getSchema, queryStores } from "./api";
import { COUNTRIES, COUNTRY_BY_CODE } from "./countries";
import type {
  ExplorerView,
  ExportJob,
  FilterCombinator,
  FilterCondition,
  FilterGroup,
  FilterNode,
  QueryFilter,
  QueryFilterNode,
  SchemaColumn,
  SortSpec,
} from "./types";

const PAGE_SIZES = [25, 50, 100, 200];
const MIN_COLUMN_WIDTH = 72;
const HEADER_WIDTH_PER_CHARACTER = 6.6;
const HEADER_CONTROLS_WIDTH = 54;
const SAVED_VIEWS_KEY = "storeleads:saved-views:v1";
const WORKSPACE_KEY = "storeleads:workspace:v1";
const COLUMN_ORDER_KEY = "storeleads:column-order:v1";
const NULL_OPERATORS = new Set(["is_null", "is_not_null"]);
const LIST_OPERATORS = new Set(["in", "not_in", "between", "has_any", "has_all"]);

type PaginationItem = number | "ellipsis";

function paginationItems(currentPage: number, totalPages: number): PaginationItem[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index + 1);
  if (currentPage <= 4) return [1, 2, 3, 4, 5, "ellipsis", totalPages];
  if (currentPage >= totalPages - 3) return [1, "ellipsis", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
  return [1, "ellipsis", currentPage - 1, currentPage, currentPage + 1, "ellipsis", totalPages];
}

const OPERATOR_LABELS: Record<string, string> = {
  eq: "is", neq: "is not", contains: "contains",
  not_contains: "does not contain", starts_with: "starts with",
  ends_with: "ends with", matches_token: "matches word", in: "is any of",
  not_in: "is none of", lt: "is less than", lte: "is at most",
  gt: "is greater than", gte: "is at least", between: "is between",
  is_null: "is empty", is_not_null: "is not empty", has: "has",
  has_any: "has any of", has_all: "has all of",
};

type IconName = "columns" | "filter" | "save" | "export" | "close" | "search" | "chevron-down";

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    columns: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/></>,
    filter: <path d="M4 5h16l-6.5 7.2V19l-3 1v-7.8L4 5Z"/>,
    save: <><path d="M5 3h12l2 2v16H5V3Z"/><path d="M8 3v6h8V3M8 21v-7h8v7"/></>,
    export: <><path d="M12 3v12M7 8l5-5 5 5"/><path d="M5 13v7h14v-7"/></>,
    close: <path d="m6 6 12 12M18 6 6 18"/>,
    search: <><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></>,
    "chevron-down": <path d="m7 9 5 5 5-5"/>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24">{paths[name]}</svg>;
}

function preferredOperator(column: SchemaColumn): string {
  if (column.facet_enabled && column.data_type === "collection" && column.filter_operators.includes("has_any")) return "has_any";
  if (column.facet_enabled && column.data_type !== "boolean" && column.filter_operators.includes("in")) return "in";
  return column.filter_operators[0] ?? "eq";
}

function makeFilter(column: SchemaColumn): FilterCondition {
  return {
    id: crypto.randomUUID(),
    column: column.name,
    operator: preferredOperator(column),
    value: column.data_type === "boolean" ? true : "",
  };
}

function isFilterGroup(node: FilterNode): node is FilterGroup {
  return "filters" in node;
}

function makeGroup(column: SchemaColumn): FilterGroup {
  return { id: crypto.randomUUID(), combinator: "or", filters: [makeFilter(column)] };
}

function countConditions(filters: FilterNode[]): number {
  return filters.reduce((total, node) => total + (isFilterGroup(node) ? countConditions(node.filters) : 1), 0);
}

function removeFilterNode(filters: FilterNode[], id: string): FilterNode[] {
  return filters
    .filter((node) => node.id !== id)
    .map((node) => isFilterGroup(node) ? { ...node, filters: removeFilterNode(node.filters, id) } : node);
}

function hydrateFilterNode(node: FilterNode): FilterNode | null {
  if (isFilterGroup(node)) {
    return {
      ...node,
      id: node.id || crypto.randomUUID(),
      combinator: node.combinator === "or" ? "or" : "and",
      filters: node.filters.map(hydrateFilterNode).filter((child): child is FilterNode => child !== null),
    };
  }
  if (!node || typeof node.column !== "string" || typeof node.operator !== "string") return null;
  return { ...node, id: node.id || crypto.randomUUID() };
}

function migrateStoredFilters(filters: FilterNode[]): { combinator: FilterCombinator; filters: FilterNode[] } {
  const hydrated = filters.map(hydrateFilterNode).filter((node): node is FilterNode => node !== null);
  const legacy = hydrated.filter((node) => !isFilterGroup(node)) as Array<FilterCondition & { combinator?: FilterCombinator }>;
  if (legacy.length !== hydrated.length || !legacy.some((filter) => filter.combinator === "or")) {
    return { combinator: "and", filters: hydrated };
  }

  const groups: FilterNode[][] = [[]];
  legacy.forEach((filter, index) => {
    if (index > 0 && filter.combinator === "or") groups.push([]);
    const { combinator: _legacyCombinator, ...condition } = filter;
    groups[groups.length - 1].push(condition);
  });
  return {
    combinator: "or",
    filters: groups.map((group) => group.length === 1
      ? group[0]
      : { id: crypto.randomUUID(), combinator: "and", filters: group }),
  };
}

function validStoredFilters(filters: FilterNode[], validColumns: Set<string>): FilterNode[] {
  return filters.flatMap((node): FilterNode[] => {
    if (!isFilterGroup(node)) return validColumns.has(node.column) ? [node] : [];
    const children = validStoredFilters(node.filters, validColumns);
    return children.length ? [{ ...node, filters: children }] : [];
  });
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const value = localStorage.getItem(key);
    return value ? (JSON.parse(value) as T) : fallback;
  } catch {
    return fallback;
  }
}

function reorderColumns(columns: string[], dragged: string, target: string): string[] {
  if (dragged === target) return columns;
  const from = columns.indexOf(dragged);
  const to = columns.indexOf(target);
  if (from === -1 || to === -1) return columns;
  const next = [...columns];
  next.splice(from, 1);
  next.splice(to, 0, dragged);
  return next;
}

function initialColumnWidth(label: string): number {
  return Math.max(MIN_COLUMN_WIDTH, Math.ceil(label.length * HEADER_WIDTH_PER_CHARACTER + HEADER_CONTROLS_WIDTH));
}

function ColumnResizer({ label, size, onResize, onAutoFit }: {
  label: string;
  size: number;
  onResize: (size: number) => void;
  onAutoFit: (header: HTMLTableCellElement) => void;
}) {
  const dragStart = useRef<{ x: number; size: number } | null>(null);
  const [resizing, setResizing] = useState(false);

  return <span
    className={`column-resizer ${resizing ? "is-resizing" : ""}`}
    draggable={false}
    role="separator"
    aria-label={`Resize ${label} column`}
    onPointerDown={(event) => {
      event.preventDefault();
      event.stopPropagation();
      dragStart.current = { x: event.clientX, size };
      setResizing(true);
      event.currentTarget.setPointerCapture(event.pointerId);
    }}
    onPointerMove={(event) => {
      if (!dragStart.current || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
      onResize(Math.max(MIN_COLUMN_WIDTH, dragStart.current.size + event.clientX - dragStart.current.x));
    }}
    onPointerUp={(event) => {
      dragStart.current = null;
      setResizing(false);
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    }}
    onPointerCancel={() => { dragStart.current = null; setResizing(false); }}
    onLostPointerCapture={() => { dragStart.current = null; setResizing(false); }}
    onDoubleClick={(event) => {
      event.stopPropagation();
      onAutoFit(event.currentTarget.parentElement as HTMLTableCellElement);
    }}
  />;
}

function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timeout = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timeout);
  }, [delay, value]);
  return debounced;
}

function normalizedValue(filter: QueryFilter, column: SchemaColumn): unknown {
  if (NULL_OPERATORS.has(filter.operator)) return undefined;
  if (column.data_type === "boolean") return filter.value === true || filter.value === "true";
  if (LIST_OPERATORS.has(filter.operator)) {
    const values = (Array.isArray(filter.value) ? filter.value : String(filter.value ?? "").split(","))
      .map((value) => String(value).trim()).filter(Boolean);
    if (["integer", "number"].includes(column.data_type)) return values.map(Number);
    return values;
  }
  if (["integer", "number"].includes(column.data_type)) return Number(filter.value);
  return filter.value;
}

function selectedValues(value: unknown): string[] {
  return (Array.isArray(value) ? value : String(value ?? "").split(","))
    .map((item) => String(item).trim())
    .filter(Boolean);
}

function valueForOperator(value: unknown, operator: string): unknown {
  if (LIST_OPERATORS.has(operator)) return selectedValues(value);
  return Array.isArray(value) ? value[0] ?? "" : value;
}

function isReady(filter: FilterCondition): boolean {
  return NULL_OPERATORS.has(filter.operator) || filter.value === true || filter.value === false || String(filter.value ?? "").trim() !== "";
}

function buildQueryFilter(node: FilterNode, columns: SchemaColumn[]): QueryFilterNode | null {
  if (isFilterGroup(node)) {
    const filters = node.filters
      .map((child) => buildQueryFilter(child, columns))
      .filter((child): child is QueryFilterNode => child !== null);
    if (filters.length === 0) return null;
    if (filters.length === 1) return filters[0];
    return { combinator: node.combinator, filters };
  }
  if (!isReady(node)) return null;
  const column = columns.find((item) => item.name === node.column);
  if (!column) return null;
  const value = normalizedValue(node, column);
  return value === undefined
    ? { column: node.column, operator: node.operator }
    : { column: node.column, operator: node.operator, value };
}

function buildFilterExpression(filters: FilterNode[], combinator: FilterCombinator, columns: SchemaColumn[]): QueryFilterNode[] {
  const ready = filters
    .map((filter) => buildQueryFilter(filter, columns))
    .filter((filter): filter is QueryFilterNode => filter !== null);
  if (ready.length === 0) return [];
  if (ready.length === 1) return ready;
  return combinator === "and" ? ready : [{ combinator: "or", filters: ready }];
}

function formatCell(value: unknown, type: SchemaColumn["data_type"]): string {
  if (value === null || value === undefined || value === "") return "—";
  if (type === "boolean") return value ? "Yes" : "No";
  if (["integer", "number", "decimal"].includes(type)) {
    const number = Number(value);
    return Number.isFinite(number)
      ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(number)
      : String(value);
  }
  return String(value);
}

function CountrySelect({ value, multiple, onChange }: {
  value: unknown;
  multiple: boolean;
  onChange: (value: unknown) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const selectedCodes = selectedValues(value)
    .map((code) => code.trim().toUpperCase())
    .filter(Boolean);
  const selected = selectedCodes.map((code) => COUNTRY_BY_CODE.get(code)).filter((country) => country !== undefined);
  const query = search.trim().toLowerCase();
  const visible = COUNTRIES.filter((country) =>
    !query || country.code.toLowerCase().includes(query) || country.name.toLowerCase().includes(query)
  );

  useEffect(() => {
    if (!open) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, [open]);

  const choose = (code: string) => {
    if (!multiple) {
      onChange(code);
      setOpen(false);
      setSearch("");
      return;
    }
    const next = selectedCodes.includes(code)
      ? selectedCodes.filter((selectedCode) => selectedCode !== code)
      : [...selectedCodes, code];
    onChange(next);
  };

  const firstSelected = selected[0];

  return <div className="country-select" ref={rootRef} onKeyDown={(event) => {
    if (event.key === "Escape") setOpen(false);
  }}>
    <button
      type="button"
      className={`country-select-trigger ${selected.length ? "has-value" : ""}`}
      aria-label="Filter value"
      aria-haspopup="listbox"
      aria-expanded={open}
      onClick={() => { setOpen((current) => !current); setSearch(""); }}
    >
      {firstSelected
        ? <span className="country-select-value">
            <img className="country-flag" src={firstSelected.flagUrl} alt="" />
            <span>{firstSelected.name} {multiple && selected.length > 1 ? `(+${selected.length - 1})` : `(${firstSelected.code})`}</span>
          </span>
        : <span className="country-select-placeholder">Select a country</span>}
      <span className="country-select-chevron" aria-hidden="true"><Icon name="chevron-down" /></span>
    </button>
    {open && <div className="country-select-menu">
      <label className="country-search"><Icon name="search" /><input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search country or code" aria-label="Search countries" /></label>
      <div className="country-options" role="listbox" aria-multiselectable={multiple || undefined}>
        {visible.map((country) => {
          const isSelected = selectedCodes.includes(country.code);
          return <button type="button" role="option" aria-selected={isSelected} className={`country-option ${isSelected ? "selected" : ""}`} key={country.code} onClick={() => choose(country.code)}>
            <img className="country-flag" src={country.flagUrl} alt="" loading="lazy" />
            <span className="country-name">{country.name}</span>
            <span className="country-code">{country.code}</span>
            {multiple && <span className="country-check" aria-hidden="true">{isSelected ? "✓" : ""}</span>}
          </button>;
        })}
        {visible.length === 0 && <p className="country-no-results">No countries found</p>}
      </div>
    </div>}
  </div>;
}

function FacetSelect({ value, column, multiple, onChange }: {
  value: unknown;
  column: SchemaColumn;
  multiple: boolean;
  onChange: (value: unknown) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const debouncedSearch = useDebouncedValue(search, 500);
  const selected = selectedValues(value);
  const facetQuery = useQuery({
    queryKey: ["facet-options", column.name, debouncedSearch],
    queryFn: () => getFacet(column.name, debouncedSearch),
    enabled: open,
    staleTime: 5 * 60_000,
    retry: false,
  });
  const fetchedOptions = (facetQuery.data?.values ?? [])
    .filter((option) => option.value !== null && option.value !== undefined && String(option.value) !== "")
    .map((option) => ({ value: String(option.value), count: option.count }));
  const fetchedValues = new Set(fetchedOptions.map((option) => option.value));
  const selectedOptions = selected
    .filter((selectedValue) => !fetchedValues.has(selectedValue) && (!debouncedSearch || selectedValue.toLowerCase().includes(debouncedSearch.toLowerCase())))
    .map((selectedValue) => ({ value: selectedValue, count: null }));
  const options = [...selectedOptions, ...fetchedOptions];

  useEffect(() => {
    if (!open) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, [open]);

  const choose = (option: string) => {
    if (!multiple) {
      onChange(option);
      setOpen(false);
      setSearch("");
      return;
    }
    onChange(selected.includes(option)
      ? selected.filter((selectedValue) => selectedValue !== option)
      : [...selected, option]);
  };
  const label = selected.length === 0
    ? `Select ${column.label.toLowerCase()}`
    : selected.length === 1
      ? selected[0]
      : `${selected.length} selected`;

  return <div className="country-select facet-select" ref={rootRef} onKeyDown={(event) => event.key === "Escape" && setOpen(false)}>
    <button type="button" className={`country-select-trigger ${selected.length ? "has-value" : ""}`} aria-label="Filter value" aria-haspopup="listbox" aria-expanded={open} onClick={() => { setOpen((current) => !current); setSearch(""); }}>
      <span className={selected.length ? "country-select-value" : "country-select-placeholder"}><span>{label}</span></span>
      <span className="country-select-chevron" aria-hidden="true"><Icon name="chevron-down" /></span>
    </button>
    {open && <div className="country-select-menu facet-select-menu">
      <label className="country-search"><Icon name="search" /><input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder={`Search ${column.label.toLowerCase()}`} aria-label={`Search ${column.label}`} /></label>
      <div className="country-options" role="listbox" aria-multiselectable={multiple || undefined}>
        {facetQuery.isPending && <p className="country-no-results">Loading values…</p>}
        {facetQuery.isError && <p className="country-no-results">Could not load values</p>}
        {!facetQuery.isPending && !facetQuery.isError && options.map((option) => {
          const isSelected = selected.includes(option.value);
          return <button type="button" role="option" aria-selected={isSelected} className={`facet-option ${isSelected ? "selected" : ""}`} key={option.value} onClick={() => choose(option.value)}>
            <span className="facet-option-name">{option.value}</span>
            {option.count === null ? <span /> : <span className="facet-option-count">{option.count.toLocaleString()}</span>}
            {multiple && <span className="country-check" aria-hidden="true">{isSelected ? "✓" : ""}</span>}
          </button>;
        })}
        {!facetQuery.isPending && !facetQuery.isError && options.length === 0 && <p className="country-no-results">No values found</p>}
      </div>
    </div>}
  </div>;
}

function FilterValue({ filter, column, onChange }: {
  filter: FilterCondition;
  column: SchemaColumn;
  onChange: (value: unknown) => void;
}) {
  if (NULL_OPERATORS.has(filter.operator)) return <span className="filter-empty">No value needed</span>;
  if (column.data_type === "boolean") {
    return <select aria-label="Filter value" value={String(filter.value)} onChange={(event) => onChange(event.target.value === "true")}><option value="true">Yes</option><option value="false">No</option></select>;
  }
  const isList = LIST_OPERATORS.has(filter.operator);
  if (column.name === "country_code") {
    return <CountrySelect value={filter.value} multiple={isList} onChange={onChange} />;
  }
  if (column.facet_enabled) {
    return <FacetSelect value={filter.value} column={column} multiple={isList} onChange={onChange} />;
  }
  const numeric = ["integer", "number", "decimal"].includes(column.data_type);
  return <input aria-label="Filter value" type={column.data_type === "date" && !isList ? "date" : numeric && !isList ? "number" : "text"} step={column.data_type === "integer" ? "1" : "any"} value={String(filter.value ?? "")} placeholder={isList ? "Comma-separated values" : column.data_type === "collection" ? "Value" : "Enter value"} onChange={(event) => onChange(event.target.value)} />;
}

function ColumnChooser({ columns, selected, onChange, onClose }: {
  columns: SchemaColumn[];
  selected: string[];
  onChange: (next: string[]) => void;
  onClose: () => void;
}) {
  const [search, setSearch] = useState("");
  const visible = columns.filter((column) => `${column.label} ${column.name}`.toLowerCase().includes(search.toLowerCase()));
  const toggle = (name: string) => {
    if (selected.includes(name) && selected.length === 1) return;
    onChange(selected.includes(name) ? selected.filter((item) => item !== name) : [...selected, name]);
  };
  return <div className="popover columns-popover">
    <div className="popover-title"><div><strong>Visible columns</strong><span>{selected.length} of {columns.length} selected</span></div><button className="icon-button" onClick={onClose} aria-label="Close column chooser"><Icon name="close" /></button></div>
    <label className="search-field"><Icon name="search" /><input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search 150 fields…" /></label>
    <div className="column-list">{visible.map((column) => <label key={column.name} className="check-row"><input type="checkbox" checked={selected.includes(column.name)} onChange={() => toggle(column.name)} /><span><b>{column.label}</b><small>{column.data_type}</small></span></label>)}</div>
    <div className="popover-actions"><button className="button button--primary" onClick={onClose}>Done</button></div>
  </div>;
}

function FilterGroupEditor({ columns, filters, combinator, depth, onChange, onCombinatorChange, onRemove }: {
  columns: SchemaColumn[];
  filters: FilterNode[];
  combinator: FilterCombinator;
  depth: number;
  onChange: (next: FilterNode[]) => void;
  onCombinatorChange: (next: FilterCombinator) => void;
  onRemove?: () => void;
}) {
  const filterable = columns.filter((column) => column.filterable);
  const defaultColumn = filterable.find((column) => column.name === "country_code") ?? filterable[0];
  const updateCondition = (id: string, patch: Partial<FilterCondition>) => onChange(filters.map((node) => node.id === id && !isFilterGroup(node) ? { ...node, ...patch } : node));
  const updateGroup = (id: string, patch: Partial<FilterGroup>) => onChange(filters.map((node) => node.id === id && isFilterGroup(node) ? { ...node, ...patch } : node));
  return <div className={`filter-group ${depth === 0 ? "filter-group--root" : "filter-group--nested"}`}>
    <div className="filter-group-heading">
      <label>Match <select aria-label={`Group ${depth + 1} matching mode`} value={combinator} onChange={(event) => onCombinatorChange(event.target.value as FilterCombinator)}><option value="and">ALL</option><option value="or">ANY</option></select> of the following <span>({combinator.toUpperCase()})</span></label>
      {onRemove && <button type="button" className="text-button remove-group" onClick={onRemove}>Remove group</button>}
    </div>
    <div className="filter-list">
      {filters.length === 0 && <div className="empty-filters"><Icon name="filter" /><p>No conditions in this group.</p></div>}
      {filters.map((node, index) => {
        const connector = index === 0 ? "Where" : combinator.toUpperCase();
        if (isFilterGroup(node)) {
          return <div className="nested-group-row" key={node.id}>
            <span className={`and-label and-label--${index === 0 ? "where" : combinator}`}>{connector}</span>
            <FilterGroupEditor columns={columns} filters={node.filters} combinator={node.combinator} depth={depth + 1} onChange={(next) => updateGroup(node.id, { filters: next })} onCombinatorChange={(next) => updateGroup(node.id, { combinator: next })} onRemove={() => onChange(filters.filter((item) => item.id !== node.id))} />
          </div>;
        }
        const column = columns.find((item) => item.name === node.column) ?? filterable[0];
        return <div className="filter-row" key={node.id}>
          <span className={`and-label and-label--${index === 0 ? "where" : combinator}`}>{connector}</span>
          <select aria-label="Filter column" value={node.column} onChange={(event) => { const nextColumn = columns.find((item) => item.name === event.target.value)!; updateCondition(node.id, { column: nextColumn.name, operator: preferredOperator(nextColumn), value: nextColumn.data_type === "boolean" ? true : "" }); }}>{filterable.map((item) => <option value={item.name} key={item.name}>{item.label}</option>)}</select>
          <select aria-label="Filter operator" value={node.operator} onChange={(event) => updateCondition(node.id, { operator: event.target.value, value: valueForOperator(node.value, event.target.value) })}>{column.filter_operators.map((operator) => <option key={operator} value={operator}>{OPERATOR_LABELS[operator] ?? operator}</option>)}</select>
          <FilterValue filter={node} column={column} onChange={(value) => updateCondition(node.id, { value })} />
          <button className="icon-button" onClick={() => onChange(filters.filter((item) => item.id !== node.id))} aria-label={`Remove ${column.label} filter`}><Icon name="close" /></button>
        </div>;
      })}
    </div>
    <div className="filter-group-actions">
      <button className="button button--quiet" onClick={() => onChange([...filters, makeFilter(defaultColumn)])}>+ Add condition</button>
      <button className="button button--quiet" disabled={depth >= 4} title={depth >= 4 ? "Groups can be nested up to five levels" : undefined} onClick={() => onChange([...filters, makeGroup(defaultColumn)])}>+ Add group</button>
    </div>
  </div>;
}

function FiltersPanel({ columns, filters, combinator, onChange, onCombinatorChange, onClose }: {
  columns: SchemaColumn[];
  filters: FilterNode[];
  combinator: FilterCombinator;
  onChange: (next: FilterNode[]) => void;
  onCombinatorChange: (next: FilterCombinator) => void;
  onClose: () => void;
}) {
  return <section className="filter-panel">
    <div className="panel-heading"><div><span className="section-kicker">Query builder</span><h2>Build grouped filter logic</h2><p>Each group acts like parentheses and can match ALL (AND) or ANY (OR).</p></div><button className="icon-button" onClick={onClose} aria-label="Close filters"><Icon name="close" /></button></div>
    <FilterGroupEditor columns={columns} filters={filters} combinator={combinator} depth={0} onChange={onChange} onCombinatorChange={onCombinatorChange} />
  </section>;
}

function FilterExpression({ filters, combinator, columns, onRemove }: {
  filters: FilterNode[];
  combinator: FilterCombinator;
  columns: SchemaColumn[];
  onRemove: (id: string) => void;
}) {
  return <>{filters.map((node, index) => <span className="expression-node" key={node.id}>
    {index > 0 && <b className={`chip-combinator chip-combinator--${combinator}`}>{combinator}</b>}
    {isFilterGroup(node)
      ? <span className="expression-group"><i>(</i><FilterExpression filters={node.filters} combinator={node.combinator} columns={columns} onRemove={onRemove} /><i>)</i></span>
      : <button onClick={() => onRemove(node.id)}>{columns.find((column) => column.name === node.column)?.label ?? node.column} {OPERATOR_LABELS[node.operator] ?? node.operator}{!NULL_OPERATORS.has(node.operator) ? ` ${String(node.value ?? "")}` : ""}<Icon name="close" /></button>}
  </span>)}</>;
}

function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.currentTarget === event.target && onClose()}><div className="modal" role="dialog" aria-modal="true" aria-label={title}><div className="modal-heading"><h2>{title}</h2><button className="icon-button" onClick={onClose} aria-label={`Close ${title}`}><Icon name="close" /></button></div>{children}</div></div>;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

function ExportModal({ columns, selectedColumns, filters, filterCount, sort, exportJobId, setExportJobId, onClose }: {
  columns: SchemaColumn[];
  selectedColumns: string[];
  filters: QueryFilterNode[];
  filterCount: number;
  sort: SortSpec[];
  exportJobId: string | null;
  setExportJobId: (value: string | null) => void;
  onClose: () => void;
}) {
  const [createdJob, setCreatedJob] = useState<ExportJob | null>(null);
  const [creating, setCreating] = useState(false);
  const [actionError, setActionError] = useState("");
  const jobQuery = useQuery({
    queryKey: ["export", exportJobId],
    queryFn: () => getExport(exportJobId!),
    enabled: Boolean(exportJobId),
    initialData: createdJob ?? undefined,
    refetchInterval: (query) => ["completed", "failed", "cancelled"].includes(query.state.data?.status ?? "") ? false : 750,
    retry: false,
  });
  const job = jobQuery.data ?? createdJob;
  const start = async () => {
    setCreating(true); setActionError("");
    try {
      const created = await createExport({ columns: selectedColumns, filters, sort });
      setCreatedJob(created); setExportJobId(created.export_id);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not create export");
    } finally {
      setCreating(false);
    }
  };
  const cancel = async () => {
    if (!job) return;
    setActionError("");
    try {
      setCreatedJob(await cancelExport(job.export_id));
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not cancel export");
    }
  };
  const active = job?.status === "queued" || job?.status === "running";
  const displayedColumns = job?.columns ?? selectedColumns;
  return <Modal title="Export results" onClose={onClose}>
    <div className="export-summary"><div><span>Scope</span><strong>All stores matching this query</strong></div><div><span>Filters</span><strong>{job ? "Saved query" : filterCount || "None"}</strong></div><div><span>Columns</span><strong>{displayedColumns.length}</strong></div><div><span>Format</span><strong>CSV</strong></div></div>
    <div className="export-columns"><span>Included columns</span><p>{displayedColumns.map((name) => columns.find((column) => column.name === name)?.label).join(" · ")}</p></div>
    {job && <div className={`export-job export-job--${job.status}`}><div className="export-job-heading"><span className={active ? "status-spinner" : "status-dot"} /><strong>{job.status === "queued" ? "Export queued" : job.status === "running" ? "Creating CSV…" : job.status === "completed" ? "Export ready" : job.status === "cancelled" ? "Export cancelled" : "Export failed"}</strong></div>{job.status === "completed" && <p>{job.row_count?.toLocaleString()} rows · {formatBytes(job.byte_size ?? 0)}</p>}{job.error && <p>{job.error}</p>}{jobQuery.isError && <p>{jobQuery.error.message}</p>}</div>}
    {!job && <div className="notice">The complete filtered result will be written directly by DuckDB. There is no row limit.</div>}
    {actionError && <div className="notice notice--error">{actionError}</div>}
    <div className="modal-actions">
      {active && <button className="button button--quiet" onClick={() => void cancel()}>Cancel export</button>}
      {!job && <button className="button button--primary" disabled={creating} onClick={() => void start()}>{creating ? "Starting…" : "Create export"}</button>}
      {job?.status === "completed" && <button className="button button--quiet" onClick={() => { setCreatedJob(null); setExportJobId(null); }}>New export</button>}
      {job?.status === "completed" && job.download_url && <a className="button button--primary" href={job.download_url}>Download CSV</a>}
      {job && !active && job.status !== "completed" && <button className="button button--primary" onClick={() => { setCreatedJob(null); setExportJobId(null); setActionError(""); }}>Try again</button>}
    </div>
  </Modal>;
}

export function App() {
  const schemaQuery = useQuery({ queryKey: ["schema"], queryFn: getSchema, staleTime: Infinity, retry: 1 });
  const columns = schemaQuery.data?.columns ?? [];
  const [selectedColumns, setSelectedColumns] = useState<string[]>([]);
  const [filters, setFilters] = useState<FilterNode[]>([]);
  const [filterCombinator, setFilterCombinator] = useState<FilterCombinator>("and");
  const [sort, setSort] = useState<SortSpec[]>([]);
  const [pageSize, setPageSize] = useState(50);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageInput, setPageInput] = useState("1");
  const [pageCursors, setPageCursors] = useState<Record<number, string | null>>({ 1: null });
  const [showColumns, setShowColumns] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [showSave, setShowSave] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [exportJobId, setExportJobId] = useState<string | null>(null);
  const [viewName, setViewName] = useState("");
  const [savedViews, setSavedViews] = useState<ExplorerView[]>(() => readJson(SAVED_VIEWS_KEY, []));
  const [hydrated, setHydrated] = useState(false);
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>({});
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>([]);
  const [draggedColumn, setDraggedColumn] = useState<string | null>(null);
  const [dragOverColumn, setDragOverColumn] = useState<string | null>(null);
  const tableScrollRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!columns.length || hydrated) return;
    const stored = readJson<Partial<ExplorerView> | null>(WORKSPACE_KEY, null);
    const validNames = new Set(columns.map((column) => column.name));
    const storedColumns = stored?.columns?.filter((name) => validNames.has(name)) ?? [];
    const selected = storedColumns.length ? storedColumns : columns.filter((column) => column.default_visible).map((column) => column.name);
    const persistedOrder = readJson<string[]>(COLUMN_ORDER_KEY, []).filter((name) => validNames.has(name));
    setSelectedColumns(selected);
    setColumnOrder([
      ...persistedOrder.filter((name) => selected.includes(name)),
      ...selected.filter((name) => !persistedOrder.includes(name)),
    ]);
    const migratedFilters = migrateStoredFilters(stored?.filters ?? []);
    setFilters(validStoredFilters(migratedFilters.filters, validNames));
    setFilterCombinator(stored?.filterCombinator ?? migratedFilters.combinator);
    setSort(stored?.sort?.filter((item) => validNames.has(item.column)) ?? []);
    setPageSize(PAGE_SIZES.includes(stored?.pageSize ?? 0) ? stored!.pageSize! : 50);
    setHydrated(true);
  }, [columns, hydrated]);

  useEffect(() => {
    if (!hydrated) return;
    localStorage.setItem(WORKSPACE_KEY, JSON.stringify({ name: "Current workspace", columns: selectedColumns, filters, filterCombinator, sort, pageSize } satisfies ExplorerView));
  }, [hydrated, selectedColumns, filters, filterCombinator, sort, pageSize]);

  useEffect(() => {
    if (!hydrated) return;
    setColumnOrder((current) => [
      ...current.filter((name) => selectedColumns.includes(name)),
      ...selectedColumns.filter((name) => !current.includes(name)),
    ]);
  }, [hydrated, selectedColumns]);

  useEffect(() => {
    if (!hydrated || !columnOrder.length) return;
    localStorage.setItem(COLUMN_ORDER_KEY, JSON.stringify(columnOrder));
  }, [columnOrder, hydrated]);

  useLayoutEffect(() => {
    if (!hydrated || !selectedColumns.length) return;
    const labels = new Map(columns.map((column) => [column.name, column.label]));
    const naturalWidths = selectedColumns.map((name) => [name, initialColumnWidth(labels.get(name) ?? name)] as const);
    const naturalTotal = naturalWidths.reduce((total, [, width]) => total + width, 0);
    const availableWidth = tableScrollRef.current?.clientWidth ?? naturalTotal;
    const extraPerColumn = Math.max(0, availableWidth - naturalTotal) / naturalWidths.length;
    setColumnSizing(Object.fromEntries(naturalWidths.map(([name, width]) => [name, width + extraPerColumn])));
  }, [columns, hydrated, selectedColumns]);

  const debouncedFilters = useDebouncedValue(filters, 600);
  const readyFilters = useMemo<QueryFilterNode[]>(
    () => buildFilterExpression(debouncedFilters, filterCombinator, columns),
    [columns, debouncedFilters, filterCombinator],
  );
  const filterCount = countConditions(filters);
  const currentCursor = pageCursors[currentPage];
  const canUseCursor = currentCursor !== undefined;
  const queryEnabled = selectedColumns.length > 0 && columns.length > 0;
  const storesQuery = useQuery({
    queryKey: ["stores", selectedColumns, readyFilters, sort, pageSize, currentPage, currentCursor],
    queryFn: () => queryStores({ columns: selectedColumns, filters: readyFilters, sort, limit: pageSize, cursor: currentCursor ?? null, offset: canUseCursor ? 0 : (currentPage - 1) * pageSize }),
    enabled: queryEnabled,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    retry: false,
  });
  const totalCount = storesQuery.data?.total_count ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  const firstRow = totalCount === 0 ? 0 : (currentPage - 1) * pageSize + 1;
  const lastRow = totalCount === 0 ? 0 : Math.min(currentPage * pageSize, totalCount);
  const visiblePages = paginationItems(currentPage, totalPages);

  useEffect(() => {
    if (storesQuery.isPlaceholderData || !storesQuery.data?.next_cursor) return;
    const nextPage = currentPage + 1;
    const nextCursor = storesQuery.data.next_cursor;
    setPageCursors((existing) => existing[nextPage] === nextCursor
      ? existing
      : { ...existing, [nextPage]: nextCursor });
  }, [currentPage, storesQuery.data?.next_cursor, storesQuery.isPlaceholderData]);

  useEffect(() => setPageInput(String(currentPage)), [currentPage]);

  const jumpToPage = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const requestedPage = Number(pageInput);
    if (!Number.isFinite(requestedPage)) {
      setPageInput(String(currentPage));
      return;
    }
    const nextPage = Math.min(totalPages, Math.max(1, Math.trunc(requestedPage)));
    setPageInput(String(nextPage));
    setCurrentPage(nextPage);
  };

  const resetPagination = () => {
    setCurrentPage(1);
    setPageCursors({ 1: null });
  };

  const tableColumns = useMemo<ColumnDef<Record<string, unknown>>[]>(() => selectedColumns.map((name) => {
    const meta = columns.find((column) => column.name === name)!;
    return { accessorKey: name, header: meta.label, size: initialColumnWidth(meta.label), cell: (context) => <span className={context.getValue() == null ? "null-value" : ""}>{formatCell(context.getValue(), meta.data_type)}</span>, enableSorting: meta.sortable };
  }), [columns, selectedColumns]);
  const sorting: SortingState = sort.map((item) => ({ id: item.column, desc: item.direction === "desc" }));
  const table = useReactTable({
    data: storesQuery.data?.rows ?? [], columns: tableColumns,
    getCoreRowModel: getCoreRowModel(), manualSorting: true, state: { sorting, columnSizing, columnOrder },
    onSortingChange: (updater) => {
      const next = typeof updater === "function" ? updater(sorting) : updater;
      setSort(next.slice(0, 5).map((item) => ({ column: item.id, direction: item.desc ? "desc" : "asc" })));
      resetPagination();
    },
    onColumnSizingChange: setColumnSizing,
    onColumnOrderChange: setColumnOrder,
    columnResizeMode: "onChange",
    defaultColumn: { size: 150, minSize: MIN_COLUMN_WIDTH, maxSize: Number.MAX_SAFE_INTEGER },
    enableMultiSort: true,
  });

  const autoFitColumn = (columnId: string, headerElement: HTMLTableCellElement) => {
    const headerButton = headerElement.querySelector<HTMLButtonElement>(".column-sort");
    const headerLabel = headerButton?.querySelector<HTMLElement>(".column-label");
    const sortMark = headerButton?.querySelector<HTMLElement>(".sort-mark");
    const buttonStyle = headerButton ? window.getComputedStyle(headerButton) : null;
    const headerWidth = (headerLabel?.scrollWidth ?? 0)
      + (sortMark?.scrollWidth ?? 0)
      + Number.parseFloat(buttonStyle?.paddingLeft ?? "0")
      + Number.parseFloat(buttonStyle?.paddingRight ?? "0")
      + Number.parseFloat(buttonStyle?.columnGap || buttonStyle?.gap || "0");
    const bodyWidths = Array.from(document.querySelectorAll<HTMLElement>(`td[data-column-id="${CSS.escape(columnId)}"]`), (cell) => cell.scrollWidth);
    setColumnSizing((current) => ({ ...current, [columnId]: Math.max(MIN_COLUMN_WIDTH, Math.ceil(Math.max(headerWidth, ...bodyWidths))) }));
  };

  const saveView = () => {
    const name = viewName.trim();
    if (!name) return;
    const next = [...savedViews.filter((view) => view.name !== name), { name, columns: selectedColumns, filters, filterCombinator, sort, pageSize }];
    setSavedViews(next);
    localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(next));
    setViewName(""); setShowSave(false);
  };
  const restoreView = (view: ExplorerView) => {
    setSelectedColumns(view.columns);
    const migrated = migrateStoredFilters(view.filters);
    setFilters(migrated.filters);
    setFilterCombinator(view.filterCombinator ?? migrated.combinator);
    setSort(view.sort); setPageSize(view.pageSize); resetPagination();
  };
  const deleteView = (name: string) => {
    const next = savedViews.filter((view) => view.name !== name);
    setSavedViews(next); localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(next));
  };

  if (schemaQuery.isPending) return <main className="state-page"><div className="loader"/><p>Opening your data workspace…</p></main>;
  if (schemaQuery.isError) return <main className="state-page"><span className="error-mark">!</span><h1>Couldn’t reach the data service</h1><p>{schemaQuery.error.message}</p><button className="button button--primary" onClick={() => void schemaQuery.refetch()}>Try again</button></main>;

  return <div className="app-shell">
    <header className="topbar"><div className="brand"><span className="brand-mark">SL</span><div><strong>Store Leads</strong><span>Explorer</span></div></div><div className="topbar-meta"><span className="connection"><i /> Local database</span><span className="schema-version">Schema v{schemaQuery.data.schema_version}</span></div></header>
    <main className="workspace">
      <div className="page-heading"><div><span className="section-kicker">Store intelligence</span><h1>Explore every store.</h1><p>Shape a precise view across {columns.length} fields. Every query runs against the full dataset.</p></div><button className="button button--primary export-button" onClick={() => setShowExport(true)}><Icon name="export" /> Export</button></div>
      <div className="toolbar">
        <div className="toolbar-group">
          <div className="relative"><button className={`button button--quiet ${showColumns ? "active" : ""}`} onClick={() => setShowColumns((value) => !value)}><Icon name="columns" /> Columns <span className="button-count">{selectedColumns.length}</span></button>{showColumns && <ColumnChooser columns={columns} selected={selectedColumns} onChange={(next) => { setSelectedColumns(next); resetPagination(); }} onClose={() => setShowColumns(false)} />}</div>
          <button className={`button button--quiet ${showFilters || filterCount ? "active" : ""}`} onClick={() => setShowFilters((value) => !value)}><Icon name="filter" /> Filters {filterCount > 0 && <span className="button-count">{filterCount}</span>}</button>
          <button className="button button--quiet" onClick={() => setShowSave(true)}><Icon name="save" /> Save view</button>
        </div>
        {savedViews.length > 0 && <select className="saved-select" aria-label="Saved views" defaultValue="" onChange={(event) => { const view = savedViews.find((item) => item.name === event.target.value); if (view) restoreView(view); event.target.value = ""; }}><option value="" disabled>Open saved view…</option>{savedViews.map((view) => <option key={view.name} value={view.name}>{view.name}</option>)}</select>}
      </div>
      {showFilters && <FiltersPanel columns={columns} filters={filters} combinator={filterCombinator} onChange={(next) => { setFilters(next); resetPagination(); }} onCombinatorChange={(next) => { setFilterCombinator(next); resetPagination(); }} onClose={() => setShowFilters(false)} />}
      {filterCount > 0 && <div className="filter-chips"><span>Active filters</span><div className="filter-expression"><FilterExpression filters={filters} combinator={filterCombinator} columns={columns} onRemove={(id) => { setFilters(removeFilterNode(filters, id)); resetPagination(); }} /></div><button className="clear-all" onClick={() => { setFilters([]); resetPagination(); }}>Clear all</button></div>}
      <section className="table-card" ref={tableScrollRef}>
        <div className="table-status"><div><strong>{storesQuery.isPending ? "Loading stores…" : `${totalCount.toLocaleString()} stores`}</strong><span>{totalCount > 0 ? `Showing ${firstRow.toLocaleString()}–${lastRow.toLocaleString()}` : "No results"}{storesQuery.isFetching && !storesQuery.isPending ? " · Updating…" : ""}</span></div><label>Rows per page<select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); resetPagination(); }}>{PAGE_SIZES.map((size) => <option key={size}>{size}</option>)}</select></label></div>
        <div className="table-viewport"><div className="table-scroll" aria-busy={storesQuery.isFetching}><table style={{ width: Math.max(table.getTotalSize(), 1) }}><thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) => <th className={`${draggedColumn === header.column.id ? "column-dragging" : ""} ${dragOverColumn === header.column.id ? "column-drag-over" : ""}`} draggable style={{ width: header.getSize() }} key={header.id} onDragStart={(event) => { setDraggedColumn(header.column.id); event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", header.column.id); }} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "move"; setDragOverColumn(header.column.id); }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragOverColumn(null); }} onDrop={(event) => { event.preventDefault(); const source = draggedColumn ?? event.dataTransfer.getData("text/plain"); setColumnOrder((current) => reorderColumns(current, source, header.column.id)); setDraggedColumn(null); setDragOverColumn(null); }} onDragEnd={() => { setDraggedColumn(null); setDragOverColumn(null); }}><button className="column-sort" disabled={!header.column.getCanSort()} onClick={header.column.getToggleSortingHandler()}><span className="column-label">{flexRender(header.column.columnDef.header, header.getContext())}</span><span className={`sort-mark ${header.column.getIsSorted() ? "sorted" : ""}`}>{header.column.getIsSorted() === "asc" ? "↑" : header.column.getIsSorted() === "desc" ? "↓" : "↕"}</span></button><ColumnResizer label={String(header.column.columnDef.header)} size={header.getSize()} onResize={(size) => setColumnSizing((current) => ({ ...current, [header.column.id]: size }))} onAutoFit={(element) => autoFitColumn(header.column.id, element)} /></th>)}</tr>)}</thead><tbody>{storesQuery.isError ? <tr><td colSpan={Math.max(selectedColumns.length, 1)}><div className="table-message table-error"><strong>Query failed</strong><span>{storesQuery.error.message}</span><button className="text-button" onClick={() => void storesQuery.refetch()}>Retry</button></div></td></tr> : storesQuery.isPending ? Array.from({ length: 8 }, (_, index) => <tr className="skeleton-row" key={index}>{table.getVisibleLeafColumns().map((column) => <td data-column-id={column.id} style={{ width: column.getSize() }} key={column.id}><span /></td>)}</tr>) : table.getRowModel().rows.length === 0 ? <tr><td colSpan={Math.max(selectedColumns.length, 1)}><div className="table-message"><strong>No stores match this view</strong><span>Try removing a filter or broadening its value.</span></div></td></tr> : table.getRowModel().rows.map((row) => <tr key={row.id}>{row.getVisibleCells().map((cell) => <td data-column-id={cell.column.id} style={{ width: cell.column.getSize() }} key={cell.id} title={String(cell.getValue() ?? "")}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody></table></div>{storesQuery.isFetching && !storesQuery.isPending && <div className="table-loading" role="status"><div className="loader"/><span>Loading page {currentPage}…</span></div>}</div>
        <nav className="pagination" aria-label="Store results pagination"><div className="pagination-summary"><span>Page {currentPage.toLocaleString()} of {totalPages.toLocaleString()}</span><form className="page-jump" onSubmit={jumpToPage}><label htmlFor="page-jump-input">Jump to</label><input id="page-jump-input" type="number" min="1" max={totalPages} inputMode="numeric" value={pageInput} disabled={storesQuery.isFetching || totalCount === 0} onChange={(event) => setPageInput(event.target.value)} onBlur={() => !pageInput && setPageInput(String(currentPage))}/><button className="button button--quiet" type="submit" disabled={storesQuery.isFetching || totalCount === 0 || !pageInput}>Go</button></form></div><div className="pagination-controls"><button className="button button--quiet pagination-step" disabled={currentPage === 1 || storesQuery.isFetching} onClick={() => setCurrentPage((page) => page - 1)}>← Previous</button><div className="page-numbers">{visiblePages.map((item, index) => item === "ellipsis" ? <span className="page-ellipsis" key={`ellipsis-${index}`}>…</span> : <button className={`page-number ${item === currentPage ? "active" : ""}`} key={item} aria-label={`Go to page ${item}`} aria-current={item === currentPage ? "page" : undefined} disabled={storesQuery.isFetching} onClick={() => setCurrentPage(item)}>{item}</button>)}</div><button className="button button--quiet pagination-step" disabled={currentPage === totalPages || storesQuery.isFetching || totalCount === 0} onClick={() => setCurrentPage((page) => page + 1)}>Next →</button></div></nav>
      </section>
    </main>
    {showSave && <Modal title="Save this view" onClose={() => setShowSave(false)}><p className="modal-copy">Save the {selectedColumns.length} visible columns, {filterCount} filters, grouped logic, sorting, and page size in this browser.</p><label className="field-label">View name<input autoFocus value={viewName} onChange={(event) => setViewName(event.target.value)} onKeyDown={(event) => event.key === "Enter" && saveView()} placeholder="e.g. High-traffic US stores" /></label>{savedViews.length > 0 && <div className="saved-list"><span>Saved views</span>{savedViews.map((view) => <div key={view.name}><button className="saved-name" onClick={() => { restoreView(view); setShowSave(false); }}>{view.name}</button><button className="icon-button" onClick={() => deleteView(view.name)} aria-label={`Delete ${view.name}`}><Icon name="close" /></button></div>)}</div>}<div className="modal-actions"><button className="button button--quiet" onClick={() => setShowSave(false)}>Cancel</button><button className="button button--primary" disabled={!viewName.trim()} onClick={saveView}>Save view</button></div></Modal>}
    {showExport && <ExportModal columns={columns} selectedColumns={selectedColumns} filters={readyFilters} filterCount={filterCount} sort={sort} exportJobId={exportJobId} setExportJobId={setExportJobId} onClose={() => setShowExport(false)} />}
  </div>;
}
