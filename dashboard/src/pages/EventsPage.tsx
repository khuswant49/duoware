import { useEffect, useState } from "react";
import { apiGet, describeError } from "../api/client";
import type { ConfigSummary, EventsResponse, EventRecord } from "../api/types";
import { wallTime } from "../components/fmt";

const EVENT_TYPES = ["registry", "layout", "operator", "camera", "system"]; // PROTOCOL.md §8 v1 types
const LIMIT = 200;

export function EventsPage({ lastId }: { lastId: number | undefined }) {
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [type, setType] = useState("");
  const [car, setCar] = useState("");
  const [cars, setCars] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<ConfigSummary>("/api/config").then((c) => setCars(c.cars.map((k) => k.name))).catch(() => undefined);
  }, []);

  useEffect(() => {
    const q = new URLSearchParams({ limit: String(LIMIT) });
    if (type) q.set("type", type);
    if (car) q.set("car", car);
    apiGet<EventsResponse>(`/api/events?${q}`).then((r) => { setEvents(r.events); setError(null); }).catch((e: unknown) => setError(describeError(e)));
  }, [type, car, lastId]);

  return (
    <div className="events-page">
      <div className="toolbar">
        <label>Type <select value={type} onChange={(e) => setType(e.target.value)}><option value="">all</option>{EVENT_TYPES.map((t) => <option key={t}>{t}</option>)}</select></label>
        <label>Car <select value={car} onChange={(e) => setCar(e.target.value)}><option value="">all</option>{cars.map((c) => <option key={c}>{c}</option>)}</select></label>
        <span className="muted">newest first · measured facts and the software reason are shown separately</span>
        {error && <span className="error">{error}</span>}
      </div>
      <table className="events">
        <thead><tr><th>#</th><th>Time</th><th>Type</th><th>Car</th><th>Who</th><th>What changed</th><th>Facts (measured)</th><th>Reason (rule applied)</th></tr></thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td>{e.id}</td>
              <td>{wallTime(e.wall_ms)}</td>
              <td>{e.type}</td>
              <td>{e.car ?? "-"}</td>
              <td>{e.operator ?? "automatic"}</td>
              <td>{e.key ?? ""}{e.value !== null ? ` → ${e.value}` : ""}{e.prev !== null ? ` (was ${e.prev})` : ""}</td>
              <td>{Object.keys(e.facts).length === 0 ? "-" : <details><summary>{Object.keys(e.facts).join(", ")}</summary><pre>{JSON.stringify(e.facts, null, 1)}</pre></details>}</td>
              <td>{e.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {events.length === 0 && <p className="muted">No events.</p>}
    </div>
  );
}
