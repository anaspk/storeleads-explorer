import {
  flexRender,
  getCoreRowModel,
  type ColumnDef,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import { cancelExport, createExport, getExport, getSchema, queryStores } from "./api";
import type {
  ExplorerView,
  ExportJob,
  FilterCondition,
  QueryFilter,
  SchemaColumn,
  SortSpec,
} from "./types";

const PAGE_SIZES = [25, 50, 100, 200];
const SAVED_VIEWS_KEY = "storeleads:saved-views:v1";
const WORKSPACE_KEY = "storeleads:workspace:v1";
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

type IconName = "columns" | "filter" | "save" | "export" | "close" | "search";

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    columns: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/></>,
    filter: <path d="M4 5h16l-6.5 7.2V19l-3 1v-7.8L4 5Z"/>,
    save: <><path d="M5 3h12l2 2v16H5V3Z"/><path d="M8 3v6h8V3M8 21v-7h8v7"/></>,
    export: <><path d="M12 3v12M7 8l5-5 5 5"/><path d="M5 13v7h14v-7"/></>,
    close: <path d="m6 6 12 12M18 6 6 18"/>,
    search: <><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24">{paths[name]}</svg>;
}

function makeFilter(column: SchemaColumn): FilterCondition {
  return {
    id: crypto.randomUUID(),
    column: column.name,
    operator: column.filter_operators[0] ?? "eq",
    value: column.data_type === "boolean" ? true : "",
  };
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const value = localStorage.getItem(key);
    return value ? (JSON.parse(value) as T) : fallback;
  } catch {
    return fallback;
  }
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
    const values = String(filter.value ?? "").split(",").map((value) => value.trim()).filter(Boolean);
    if (["integer", "number"].includes(column.data_type)) return values.map(Number);
    return values;
  }
  if (["integer", "number"].includes(column.data_type)) return Number(filter.value);
  return filter.value;
}

function isReady(filter: FilterCondition): boolean {
  return NULL_OPERATORS.has(filter.operator) || filter.value === true || filter.value === false || String(filter.value ?? "").trim() !== "";
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
    <div className="popover-actions"><button className="text-button" onClick={() => onChange(columns.filter((column) => column.default_visible).map((column) => column.name))}>Reset defaults</button><button className="button button--primary" onClick={onClose}>Done</button></div>
  </div>;
}

function FiltersPanel({ columns, filters, onChange, onClose }: {
  columns: SchemaColumn[];
  filters: FilterCondition[];
  onChange: (next: FilterCondition[]) => void;
  onClose: () => void;
}) {
  const filterable = columns.filter((column) => column.filterable);
  const update = (id: string, patch: Partial<FilterCondition>) => onChange(filters.map((filter) => filter.id === id ? { ...filter, ...patch } : filter));
  const add = () => onChange([...filters, makeFilter(filterable.find((column) => column.name === "country_code") ?? filterable[0])]);
  return <section className="filter-panel">
    <div className="panel-heading"><div><span className="section-kicker">Query builder</span><h2>All of these conditions must match</h2></div><button className="icon-button" onClick={onClose} aria-label="Close filters"><Icon name="close" /></button></div>
    <div className="filter-list">
      {filters.length === 0 && <div className="empty-filters"><Icon name="filter" /><p>No filters applied. Browse every store or add a condition.</p></div>}
      {filters.map((filter, index) => {
        const column = columns.find((item) => item.name === filter.column) ?? filterable[0];
        return <div className="filter-row" key={filter.id}>
          <span className="and-label">{index === 0 ? "Where" : "And"}</span>
          <select aria-label="Filter column" value={filter.column} onChange={(event) => { const nextColumn = columns.find((item) => item.name === event.target.value)!; update(filter.id, { column: nextColumn.name, operator: nextColumn.filter_operators[0], value: nextColumn.data_type === "boolean" ? true : "" }); }}>{filterable.map((item) => <option value={item.name} key={item.name}>{item.label}</option>)}</select>
          <select aria-label="Filter operator" value={filter.operator} onChange={(event) => update(filter.id, { operator: event.target.value })}>{column.filter_operators.map((operator) => <option key={operator} value={operator}>{OPERATOR_LABELS[operator] ?? operator}</option>)}</select>
          <FilterValue filter={filter} column={column} onChange={(value) => update(filter.id, { value })} />
          <button className="icon-button" onClick={() => onChange(filters.filter((item) => item.id !== filter.id))} aria-label={`Remove ${column.label} filter`}><Icon name="close" /></button>
        </div>;
      })}
    </div>
    <button className="button button--quiet add-filter" onClick={add}>+ Add condition</button>
  </section>;
}

function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.currentTarget === event.target && onClose()}><div className="modal" role="dialog" aria-modal="true" aria-label={title}><div className="modal-heading"><h2>{title}</h2><button className="icon-button" onClick={onClose} aria-label={`Close ${title}`}><Icon name="close" /></button></div>{children}</div></div>;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

function ExportModal({ columns, selectedColumns, filters, sort, exportJobId, setExportJobId, onClose }: {
  columns: SchemaColumn[];
  selectedColumns: string[];
  filters: QueryFilter[];
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
    <div className="export-summary"><div><span>Scope</span><strong>All stores matching this query</strong></div><div><span>Filters</span><strong>{job ? "Saved query" : filters.length || "None"}</strong></div><div><span>Columns</span><strong>{displayedColumns.length}</strong></div><div><span>Format</span><strong>CSV</strong></div></div>
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
  const [filters, setFilters] = useState<FilterCondition[]>([]);
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

  useEffect(() => {
    if (!columns.length || hydrated) return;
    const stored = readJson<Partial<ExplorerView> | null>(WORKSPACE_KEY, null);
    const validNames = new Set(columns.map((column) => column.name));
    const storedColumns = stored?.columns?.filter((name) => validNames.has(name)) ?? [];
    setSelectedColumns(storedColumns.length ? storedColumns : columns.filter((column) => column.default_visible).map((column) => column.name));
    setFilters(stored?.filters?.filter((filter) => validNames.has(filter.column)) ?? []);
    setSort(stored?.sort?.filter((item) => validNames.has(item.column)) ?? []);
    setPageSize(PAGE_SIZES.includes(stored?.pageSize ?? 0) ? stored!.pageSize! : 50);
    setHydrated(true);
  }, [columns, hydrated]);

  useEffect(() => {
    if (!hydrated) return;
    localStorage.setItem(WORKSPACE_KEY, JSON.stringify({ name: "Current workspace", columns: selectedColumns, filters, sort, pageSize } satisfies ExplorerView));
  }, [hydrated, selectedColumns, filters, sort, pageSize]);

  const debouncedFilters = useDebouncedValue(filters, 350);
  const readyFilters = useMemo<QueryFilter[]>(() => debouncedFilters.filter(isReady).map(({ id: _id, ...filter }) => {
    const column = columns.find((item) => item.name === filter.column)!;
    const value = normalizedValue(filter, column);
    return value === undefined ? { column: filter.column, operator: filter.operator } : { column: filter.column, operator: filter.operator, value };
  }), [columns, debouncedFilters]);
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
    return { accessorKey: name, header: meta.label, cell: (context) => <span className={context.getValue() == null ? "null-value" : ""}>{formatCell(context.getValue(), meta.data_type)}</span>, enableSorting: meta.sortable };
  }), [columns, selectedColumns]);
  const sorting: SortingState = sort.map((item) => ({ id: item.column, desc: item.direction === "desc" }));
  const table = useReactTable({
    data: storesQuery.data?.rows ?? [], columns: tableColumns,
    getCoreRowModel: getCoreRowModel(), manualSorting: true, state: { sorting },
    onSortingChange: (updater) => {
      const next = typeof updater === "function" ? updater(sorting) : updater;
      setSort(next.slice(0, 5).map((item) => ({ column: item.id, direction: item.desc ? "desc" : "asc" })));
      resetPagination();
    },
    enableMultiSort: true,
  });

  const saveView = () => {
    const name = viewName.trim();
    if (!name) return;
    const next = [...savedViews.filter((view) => view.name !== name), { name, columns: selectedColumns, filters, sort, pageSize }];
    setSavedViews(next);
    localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(next));
    setViewName(""); setShowSave(false);
  };
  const restoreView = (view: ExplorerView) => {
    setSelectedColumns(view.columns);
    setFilters(view.filters.map((filter) => ({ ...filter, id: crypto.randomUUID() })));
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
          <button className={`button button--quiet ${showFilters || filters.length ? "active" : ""}`} onClick={() => setShowFilters((value) => !value)}><Icon name="filter" /> Filters {filters.length > 0 && <span className="button-count">{filters.length}</span>}</button>
          <button className="button button--quiet" onClick={() => setShowSave(true)}><Icon name="save" /> Save view</button>
        </div>
        {savedViews.length > 0 && <select className="saved-select" aria-label="Saved views" defaultValue="" onChange={(event) => { const view = savedViews.find((item) => item.name === event.target.value); if (view) restoreView(view); event.target.value = ""; }}><option value="" disabled>Open saved view…</option>{savedViews.map((view) => <option key={view.name} value={view.name}>{view.name}</option>)}</select>}
      </div>
      {showFilters && <FiltersPanel columns={columns} filters={filters} onChange={(next) => { setFilters(next); resetPagination(); }} onClose={() => setShowFilters(false)} />}
      {filters.length > 0 && <div className="filter-chips"><span>Active filters</span>{filters.map((filter) => { const column = columns.find((item) => item.name === filter.column)!; return <button key={filter.id} onClick={() => { setFilters(filters.filter((item) => item.id !== filter.id)); resetPagination(); }}>{column.label} {OPERATOR_LABELS[filter.operator]}{!NULL_OPERATORS.has(filter.operator) ? ` ${String(filter.value ?? "")}` : ""}<Icon name="close" /></button>; })}<button className="clear-all" onClick={() => { setFilters([]); resetPagination(); }}>Clear all</button></div>}
      <section className="table-card">
        <div className="table-status"><div><strong>{storesQuery.isPending ? "Loading stores…" : `${totalCount.toLocaleString()} stores`}</strong><span>{totalCount > 0 ? `Showing ${firstRow.toLocaleString()}–${lastRow.toLocaleString()}` : "No results"}{storesQuery.isFetching && !storesQuery.isPending ? " · Updating…" : ""}</span></div><label>Rows per page<select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); resetPagination(); }}>{PAGE_SIZES.map((size) => <option key={size}>{size}</option>)}</select></label></div>
        <div className="table-viewport"><div className="table-scroll" aria-busy={storesQuery.isFetching}><table><thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) => <th key={header.id}><button disabled={!header.column.getCanSort()} onClick={header.column.getToggleSortingHandler()}>{flexRender(header.column.columnDef.header, header.getContext())}<span className={`sort-mark ${header.column.getIsSorted() ? "sorted" : ""}`}>{header.column.getIsSorted() === "asc" ? "↑" : header.column.getIsSorted() === "desc" ? "↓" : "↕"}</span></button></th>)}</tr>)}</thead><tbody>{storesQuery.isError ? <tr><td colSpan={Math.max(selectedColumns.length, 1)}><div className="table-message table-error"><strong>Query failed</strong><span>{storesQuery.error.message}</span><button className="text-button" onClick={() => void storesQuery.refetch()}>Retry</button></div></td></tr> : storesQuery.isPending ? Array.from({ length: 8 }, (_, index) => <tr className="skeleton-row" key={index}>{selectedColumns.map((column) => <td key={column}><span /></td>)}</tr>) : table.getRowModel().rows.length === 0 ? <tr><td colSpan={Math.max(selectedColumns.length, 1)}><div className="table-message"><strong>No stores match this view</strong><span>Try removing a filter or broadening its value.</span></div></td></tr> : table.getRowModel().rows.map((row) => <tr key={row.id}>{row.getVisibleCells().map((cell) => <td key={cell.id} title={String(cell.getValue() ?? "")}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody></table></div>{storesQuery.isFetching && !storesQuery.isPending && <div className="table-loading" role="status"><div className="loader"/><span>Loading page {currentPage}…</span></div>}</div>
        <nav className="pagination" aria-label="Store results pagination"><div className="pagination-summary"><span>Page {currentPage.toLocaleString()} of {totalPages.toLocaleString()}</span><form className="page-jump" onSubmit={jumpToPage}><label htmlFor="page-jump-input">Jump to</label><input id="page-jump-input" type="number" min="1" max={totalPages} inputMode="numeric" value={pageInput} disabled={storesQuery.isFetching || totalCount === 0} onChange={(event) => setPageInput(event.target.value)} onBlur={() => !pageInput && setPageInput(String(currentPage))}/><button className="button button--quiet" type="submit" disabled={storesQuery.isFetching || totalCount === 0 || !pageInput}>Go</button></form></div><div className="pagination-controls"><button className="button button--quiet pagination-step" disabled={currentPage === 1 || storesQuery.isFetching} onClick={() => setCurrentPage((page) => page - 1)}>← Previous</button><div className="page-numbers">{visiblePages.map((item, index) => item === "ellipsis" ? <span className="page-ellipsis" key={`ellipsis-${index}`}>…</span> : <button className={`page-number ${item === currentPage ? "active" : ""}`} key={item} aria-label={`Go to page ${item}`} aria-current={item === currentPage ? "page" : undefined} disabled={storesQuery.isFetching} onClick={() => setCurrentPage(item)}>{item}</button>)}</div><button className="button button--quiet pagination-step" disabled={currentPage === totalPages || storesQuery.isFetching || totalCount === 0} onClick={() => setCurrentPage((page) => page + 1)}>Next →</button></div></nav>
      </section>
    </main>
    {showSave && <Modal title="Save this view" onClose={() => setShowSave(false)}><p className="modal-copy">Save the {selectedColumns.length} visible columns, {filters.length} filters, sorting, and page size in this browser.</p><label className="field-label">View name<input autoFocus value={viewName} onChange={(event) => setViewName(event.target.value)} onKeyDown={(event) => event.key === "Enter" && saveView()} placeholder="e.g. High-traffic US stores" /></label>{savedViews.length > 0 && <div className="saved-list"><span>Saved views</span>{savedViews.map((view) => <div key={view.name}><button className="saved-name" onClick={() => { restoreView(view); setShowSave(false); }}>{view.name}</button><button className="icon-button" onClick={() => deleteView(view.name)} aria-label={`Delete ${view.name}`}><Icon name="close" /></button></div>)}</div>}<div className="modal-actions"><button className="button button--quiet" onClick={() => setShowSave(false)}>Cancel</button><button className="button button--primary" disabled={!viewName.trim()} onClick={saveView}>Save view</button></div></Modal>}
    {showExport && <ExportModal columns={columns} selectedColumns={selectedColumns} filters={readyFilters} sort={sort} exportJobId={exportJobId} setExportJobId={setExportJobId} onClose={() => setShowExport(false)} />}
  </div>;
}
