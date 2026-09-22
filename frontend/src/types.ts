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
};

export type QueryResponse = {
  rows: Record<string, unknown>[];
  next_cursor: string | null;
};

export type ExplorerView = {
  name: string;
  columns: string[];
  filters: FilterCondition[];
  sort: SortSpec[];
  pageSize: number;
};

export type ApiErrorBody = { error?: { code?: string; message?: string } };
