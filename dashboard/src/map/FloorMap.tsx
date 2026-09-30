import type { CarState, ConfigSummary, Layout, NodeStateName, StateMsg, TagEntry } from "../api/types";
import { headingInGrid, makeTransform, type Pt } from "./transform";

const W = 900;
const H = 560;
const DEFAULT_CAR_MM: [number, number] = [200, 160];
const NODE_R = 14; // SVG units
const STATE_COLOR: Record<NodeStateName, string> = {
  ok: "var(--ok)",
  unmeasured: "var(--muted)",
  blocked: "var(--bad)",
  moved: "var(--warn)",
  outside_view: "var(--info)",
};
const KIND_ICON: Record<string, string> = { pickup: "▲", dropoff: "▼", home: "⌂", charging: "⚡", custom: "★" };

export interface FloorMapProps {
  layout: Layout | null;
  state: StateMsg | null;
  config: ConfigSummary | null;
  tagDocs: TagEntry[];
  onToggleNode: (id: number, blockedByAdmin: boolean) => void;
  onToggleEdge: (a: number, b: number, blockedByAdmin: boolean) => void;
}

function pts(points: Pt[]): string {
  return points.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
}

export function FloorMap({ layout, state, config, tagDocs, onToggleNode, onToggleEdge }: FloorMapProps) {
  const rot = layout?.grid_rotation_deg ?? 0;
  const live = new Map((state?.tags ?? []).map((t) => [t.id, t]));
  const docs = new Map(tagDocs.map((t) => [t.id, t]));
  const nodePos = (id: number, x: number | null, y: number | null): Pt | null => {
    if (x !== null && y !== null) return [x, y];
    const t = live.get(id);
    return t && t.x_mm !== null && t.y_mm !== null ? [t.x_mm, t.y_mm] : null;
  };

  const all: Pt[] = [];
  for (const f of layout?.footprints ?? []) for (const p of f.polygon_mm) all.push([p[0], p[1]]);
  for (const n of layout?.nodes ?? []) {
    const p = nodePos(n.id, n.x_mm, n.y_mm);
    if (p) all.push(p);
  }
  for (const t of state?.tags ?? []) if (t.x_mm !== null && t.y_mm !== null) all.push([t.x_mm, t.y_mm]);
  for (const c of state?.cars ?? []) if (c.pose) all.push([c.pose.x_mm, c.pose.y_mm]);
  const tf = makeTransform(all, rot, W, H);

  const nodePoint = new Map<number, Pt>();
  for (const n of layout?.nodes ?? []) {
    const p = nodePos(n.id, n.x_mm, n.y_mm);
    if (p) nodePoint.set(n.id, tf.toSvg(p));
  }
  const gridIds = new Set((layout?.nodes ?? []).map((n) => n.id));
  const atNode = new Set((state?.cars ?? []).map((c) => c.pose?.at_node).filter((v): v is number => v !== null && v !== undefined));
  const defaultRadius = config?.layout_rules.tracking["obstacle_block_radius_mm"] ?? 150;

  const carShape = (c: CarState) => {
    if (!c.pose) return null;
    const [len, wid] = config?.cars.find((k) => k.name === c.name)?.footprint_mm ?? DEFAULT_CAR_MM;
    const [x, y] = tf.toSvg([c.pose.x_mm, c.pose.y_mm]);
    const L = tf.lengthToSvg(len);
    const Wd = tf.lengthToSvg(wid);
    return (
      <g key={c.name} transform={`translate(${x} ${y}) rotate(${headingInGrid(c.pose.heading_deg, rot)})`} opacity={c.pose.fresh ? 1 : 0.35}>
        <polygon points={`${L / 2},0 ${-L / 2},${-Wd / 2} ${-L / 2},${Wd / 2}`} fill={c.color} stroke="var(--fg)" strokeWidth={1} />
        <title>{`${c.name}: age ${c.pose.age_ms.toFixed(0)} ms${c.pose.fresh ? "" : " (stale)"}`}</title>
      </g>
    );
  };

  return (
    <svg className="floor-map" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Floor map">
      {(layout?.footprints ?? []).map((f) => (
        <polygon key={f.cam} points={pts(f.polygon_mm.map((p) => tf.toSvg([p[0], p[1]])))} className="footprint">
          <title>{`Camera ${f.cam} view`}</title>
        </polygon>
      ))}

      {(layout?.edges ?? []).map((e) => {
        const a = nodePoint.get(e.a);
        const b = nodePoint.get(e.b);
        if (!a || !b) return null;
        const blocked = e.state === "blocked";
        return (
          <g key={`${e.a}-${e.b}`} onClick={() => onToggleEdge(e.a, e.b, e.blocked_by === "admin")} className="edge">
            <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke="transparent" strokeWidth={14} />
            <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke={blocked ? "var(--bad)" : "var(--ok)"} strokeWidth={blocked ? 4 : 3}
              strokeDasharray={e.length_mm === null ? "6 5" : undefined} />
            <title>{e.length_mm === null ? `${e.a}-${e.b}: not measured` : `${e.a}-${e.b}: ${e.length_mm.toFixed(0)} mm, ${e.bearing_deg?.toFixed(1)}° (error ${e.angle_error_deg?.toFixed(1)}°)${blocked ? `, blocked by ${e.blocked_by}` : ""}`}</title>
          </g>
        );
      })}

      {(state?.tags ?? [])
        .filter((t) => !gridIds.has(t.id) && t.seen && t.x_mm !== null && t.y_mm !== null && t.role !== "car")
        .map((t) => {
          const [x, y] = tf.toSvg([t.x_mm as number, t.y_mm as number]);
          if (t.role === "obstacle") {
            const r = tf.lengthToSvg(docs.get(t.id)?.radius_mm ?? defaultRadius);
            return (
              <g key={t.id}>
                <circle cx={x} cy={y} r={r} className="obstacle" />
                <text x={x} y={y + 4} textAnchor="middle" className="tiny">{t.id}</text>
              </g>
            );
          }
          if (t.role === "anchor") {
            return (
              <g key={t.id}>
                <rect x={x - 8} y={y - 8} width={16} height={16} transform={`rotate(45 ${x} ${y})`} className="anchor" />
                <text x={x} y={y + 22} textAnchor="middle" className="tiny">{t.id}</text>
              </g>
            );
          }
          return (
            <g key={t.id} opacity={0.7}>
              <rect x={x - 6} y={y - 6} width={12} height={12} className="unassigned" />
              <text x={x} y={y + 20} textAnchor="middle" className="tiny">{t.id}{t.role === "unassigned" ? "" : ` ${t.role}`}</text>
            </g>
          );
        })}

      {(layout?.nodes ?? []).map((n) => {
        const p = nodePoint.get(n.id);
        if (!p) return null;
        return (
          <g key={n.id} onClick={() => onToggleNode(n.id, n.blocked_by === "admin")} className="node">
            {atNode.has(n.id) && <circle cx={p[0]} cy={p[1]} r={NODE_R + 7} className="at-node" />}
            <circle cx={p[0]} cy={p[1]} r={NODE_R} fill={STATE_COLOR[n.state]} stroke="var(--fg)" strokeWidth={1.5} />
            <text x={p[0]} y={p[1] + 4} textAnchor="middle" className="node-label">{n.station ? KIND_ICON[n.station.kind] : ""}</text>
            <text x={p[0]} y={p[1] - NODE_R - 6} textAnchor="middle" className="tiny">{n.grid[0]},{n.grid[1]} · #{n.id}</text>
            {n.station && <text x={p[0]} y={p[1] + NODE_R + 14} textAnchor="middle" className="tiny">{n.station.name}</text>}
            <title>{`Node ${n.id} (${n.grid[0]},${n.grid[1]}): ${n.state}${n.blocked_by ? ` by ${n.blocked_by}` : ""}. Click to block or unblock.`}</title>
          </g>
        );
      })}

      {(state?.cars ?? []).map(carShape)}
      {(state?.cars ?? []).map((c) => {
        if (!c.pose) return null;
        const [x, y] = tf.toSvg([c.pose.x_mm, c.pose.y_mm]);
        return (
          <text key={`l-${c.name}`} x={x} y={y - 22} textAnchor="middle" className="car-label" opacity={c.pose.fresh ? 1 : 0.5}>
            {c.name} · {c.pose.age_ms.toFixed(0)} ms
          </text>
        );
      })}
      {!layout && <text x={W / 2} y={H / 2} textAnchor="middle" className="muted">Waiting for the server…</text>}
    </svg>
  );
}
