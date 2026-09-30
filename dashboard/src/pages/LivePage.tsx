import { useEffect, useState } from "react";
import { apiGet, apiSend, describeError } from "../api/client";
import type { ConfigSummary, EventRecord, Layout, StateMsg, TagEntry, TagList } from "../api/types";
import { CameraPanel } from "../components/CameraPanel";
import { wallTime } from "../components/fmt";
import { FloorMap } from "../map/FloorMap";

const LAYOUT_POLL_MS = 1000; // node states (moved, blocked, outside view) change without a layout version change
const EVENTS_SHOWN = 20;

interface Props {
  state: StateMsg | null;
  lastEvents: EventRecord[];
  stale: boolean; // no live data: the map and the camera panel are dimmed (M1 review F2)
}

export function LivePage({ state, lastEvents, stale }: Props) {
  const [layout, setLayout] = useState<Layout | null>(null);
  const [config, setConfig] = useState<ConfigSummary | null>(null);
  const [tagDocs, setTagDocs] = useState<TagEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const registryVersion = state?.system.registry_version;

  useEffect(() => {
    apiGet<ConfigSummary>("/api/config").then(setConfig).catch((e: unknown) => setError(describeError(e)));
  }, []);

  useEffect(() => {
    let alive = true;
    const load = () =>
      apiGet<Layout>("/api/layout")
        .then((l) => alive && setLayout(l))
        .catch((e: unknown) => alive && setError(describeError(e)));
    load();
    const t = setInterval(load, LAYOUT_POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    apiGet<TagList>("/api/tags").then((l) => setTagDocs(l.tags)).catch(() => undefined);
  }, [registryVersion]);

  const putBlocked = async (nodes: number[], edges: number[][]) => {
    if (!layout) return;
    setError(null);
    try {
      setLayout(await apiSend<Layout>("PUT", "/api/layout/blocked", { nodes, edges, expected_layout_version: layout.version }));
    } catch (e) {
      setError(describeError(e));
    }
  };

  const adminNodes = () => (layout?.nodes ?? []).filter((n) => n.blocked_by === "admin").map((n) => n.id);
  const adminEdges = () => (layout?.edges ?? []).filter((e) => e.blocked_by === "admin").map((e) => [e.a, e.b]);

  const onToggleNode = (id: number, blocked: boolean) => {
    if (!window.confirm(`${blocked ? "Unblock" : "Block"} node ${id}?`)) return;
    const nodes = blocked ? adminNodes().filter((n) => n !== id) : [...adminNodes(), id];
    void putBlocked(nodes, adminEdges());
  };
  const onToggleEdge = (a: number, b: number, blocked: boolean) => {
    if (!window.confirm(`${blocked ? "Unblock" : "Block"} the edge ${a}-${b}?`)) return;
    const edges = blocked ? adminEdges().filter((e) => !(e[0] === a && e[1] === b)) : [...adminEdges(), [a, b]];
    void putBlocked(adminNodes(), edges);
  };

  return (
    <div className={`live${stale ? " stale" : ""}`}>
      <div className="live-main">
        <FloorMap layout={layout} state={state} config={config} tagDocs={tagDocs} onToggleNode={onToggleNode} onToggleEdge={onToggleEdge} />
        {error && <p className="error">{error}</p>}
        {layout && layout.warnings.length > 0 && (
          <ul className="warnings">
            {layout.warnings.map((w, i) => (
              <li key={i} title={w.code}>{w.message}</li>
            ))}
          </ul>
        )}
        <ul className="cars">
          {(state?.cars ?? []).map((c) => (
            <li key={c.name}>
              <span className="swatch" style={{ background: c.color }} /> <b>{c.name}</b> tag {c.tag ?? "-"} · link {c.link.state} ·{" "}
              {c.pose ? `pose age ${c.pose.age_ms.toFixed(0)} ms${c.pose.fresh ? "" : " (stale)"}${c.pose.at_node !== null ? `, at node ${c.pose.at_node}` : ""}` : "no pose"} ·
              stopped: {c.stopped_reason ?? "no"} · battery {c.battery.replace("_", " ")}
            </li>
          ))}
        </ul>
      </div>
      <aside className="live-side">
        {(state?.cameras ?? []).map((c) => (
          <CameraPanel key={c.cam} cam={c} />
        ))}
        {state && state.cameras.length === 0 && <p className="muted">No camera has paired yet. Pair the phone with the code in the top bar.</p>}
        <section className="panel">
          <h3>Latest events</h3>
          <ul className="events-mini">
            {lastEvents.slice(0, EVENTS_SHOWN).map((e) => (
              <li key={e.id}>
                <span className="muted">{wallTime(e.wall_ms)}</span> <b>{e.type}</b> {e.key ?? ""} {e.value ?? ""} <span className="muted">{e.reason}</span>
              </li>
            ))}
          </ul>
        </section>
      </aside>
    </div>
  );
}
