import { useEffect, useState } from "react";
import { fetchHealth, protocolMatches, type Health } from "./api/health";

/** M0 skeleton: shows whether the server is reachable. The real layout arrives in M1. */
export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    fetchHealth(ctl.signal)
      .then(setHealth)
      .catch((e: unknown) => {
        if (!ctl.signal.aborted) setError(String(e));
      });
    return () => ctl.abort();
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: 24 }}>
      <h1>DUO-WARE 2</h1>
      {health && (
        <p>
          Server {health.version}, mode <b>{health.mode}</b>, protocol v{health.proto}
          {!protocolMatches(health) && " (MISMATCH: update the dashboard)"}
        </p>
      )}
      {error && <p style={{ color: "crimson" }}>Server not reachable: {error}</p>}
      {!health && !error && <p>Connecting...</p>}
    </main>
  );
}
