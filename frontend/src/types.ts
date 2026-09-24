export type DataType =
  | "string"
  | "integer"
  | "number"
  | "decimal"
  | "boolean"
  | "date"
  | "uuid"
  | "collection";

export type SchemaColumn = {
  name: string;
  label: string;
  data_type: DataType;
  filter_operators: string[];
  sortable: boolean;
  filterable: boolean;
  default_visible: boolean;
  facet_enabled: boolean;
};

export type SchemaResponse = {
  schema_version: number;
  columns: SchemaColumn[];
};

export type FilterCondition = {
  id: string;
  column: string;
  operator: string;
  value?: unknown;
};

export type QueryFilter = Omit<FilterCondition, "id">;
export type SortSpec = { column: string; direction: "asc" | "desc" };

export type QueryRequest = {
  columns: string[];
  filters: QueryFilter[];
  sort: SortSpec[];
  limit: number;
  cursor: string | null;
  offset?: number;
};

export type QueryResponse = {
  rows: Record<string, unknown>[];
  next_cursor: string | null;
  total_count: number;
};

export type ExportRequest = Pick<QueryRequest, "columns" | "filters" | "sort">;
export type ExportStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export type ExportJob = {
  export_id: string;
  status: ExportStatus;
  columns: string[];
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  row_count: number | null;
  byte_size: number | null;
  error: string | null;
  download_url: string | null;
};

export type ExplorerView = {
  name: string;
  columns: string[];
  filters: FilterCondition[];
  sort: SortSpec[];
  pageSize: number;
};

export type ApiErrorBody = { error?: { code?: string; message?: string } };
