import type { ApiErrorBody, ExportJob, ExportRequest, QueryRequest, QueryResponse, SchemaResponse } from "./types";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    try {
      const body = (await response.json()) as ApiErrorBody;
      message = body.error?.message ?? message;
    } catch {
      // Keep the status fallback when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export function getSchema(): Promise<SchemaResponse> {
  return requestJson<SchemaResponse>("/api/schema");
}

export function queryStores(payload: QueryRequest): Promise<QueryResponse> {
  return requestJson<QueryResponse>("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function createExport(payload: ExportRequest): Promise<ExportJob> {
  return requestJson<ExportJob>("/api/exports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function getExport(exportId: string): Promise<ExportJob> {
  return requestJson<ExportJob>(`/api/exports/${exportId}`);
}

export function cancelExport(exportId: string): Promise<ExportJob> {
  return requestJson<ExportJob>(`/api/exports/${exportId}/cancel`, { method: "POST" });
}
