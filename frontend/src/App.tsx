import { useQuery } from "@tanstack/react-query";

type HealthResponse = {
  status: "ok";
};

async function getHealth(): Promise<HealthResponse> {
  const response = await fetch("/api/health");
  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`);
  }
  return response.json() as Promise<HealthResponse>;
}

export function App() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    retry: 1,
  });

  const apiState = health.isPending
    ? "Checking API…"
    : health.isError
      ? "API unavailable"
      : "API connected";

  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">Local data workspace</p>
        <h1>Store Leads Explorer</h1>
        <p className="summary">
          The project foundation is ready. Dataset profiling, ingestion, and the
          searchable explorer will arrive in the next phases.
        </p>
        <div className={`status ${health.isError ? "status--error" : ""}`}>
          <span aria-hidden="true" className="status__dot" />
          <span>{apiState}</span>
        </div>
      </section>
    </main>
  );
}

