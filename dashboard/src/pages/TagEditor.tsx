import { useState } from "react";
import { apiSend, describeError } from "../api/client";
import { blankBody } from "../api/tagbody";
import type { ConfigSummary, Role, StationKind, TagBody, TagEntry } from "../api/types";

interface Props {
  entry: { id: number; version: number; body: TagBody | null };
  config: ConfigSummary;
  onDone: (message: string) => void;
  onCancel: () => void;
}

const ROLE_CHOICES: (Role | "unassigned")[] = ["unassigned", "car", "node", "anchor", "obstacle", "ignore"];

function numOrNull(s: string): number | null {
  if (s.trim() === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

function intOrNull(s: string): number | null {
  const n = numOrNull(s);
  return n === null ? null : Math.trunc(n);
}

export function TagEditor({ entry, config, onDone, onCancel }: Props) {
  const defaultSize = config.markers.default_size_mm;
  const [role, setRole] = useState<Role | "unassigned">(entry.body?.role ?? "unassigned");
  const [d, setD] = useState<TagBody>(entry.body ?? blankBody("ignore", defaultSize));
  const [error, setError] = useState<string | null>(null);
  const set = (patch: Partial<TagBody>) => setD((p) => ({ ...p, ...patch }));

  const changeRole = (r: Role | "unassigned") => {
    setRole(r);
    if (r !== "unassigned") setD({ ...blankBody(r, defaultSize, config.cars[0]?.name), label: d.label ?? null });
  };

  const save = async () => {
    setError(null);
    try {
      if (role === "unassigned") {
        if (entry.version > 0) await apiSend("DELETE", `/api/tags/${entry.id}?expected_version=${entry.version}`);
        onDone(`Tag ${entry.id} is unassigned.`);
      } else {
        await apiSend<TagEntry>("PUT", `/api/tags/${entry.id}`, { ...d, role, expected_version: entry.version });
        onDone(`Tag ${entry.id} saved as ${role}.`);
      }
    } catch (e) {
      setError(describeError(e));
    }
  };

  const pose = d.pose;
  const setPose = (k: "x_mm" | "y_mm" | "yaw_deg", v: string) => {
    const n = numOrNull(v);
    const p = { x_mm: pose?.x_mm ?? 0, y_mm: pose?.y_mm ?? 0, yaw_deg: pose?.yaw_deg ?? 0, [k]: n ?? 0 };
    set({ pose: v.trim() === "" && !pose ? null : p });
  };

  return (
    <div className="editor">
      <label>
        Role{" "}
        <select value={role} onChange={(e) => changeRole(e.target.value as Role | "unassigned")}>
          {ROLE_CHOICES.map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
      </label>
      {role !== "unassigned" && (
        <>
          <label>
            Printed size (mm) <input type="number" value={d.size_mm ?? ""} onChange={(e) => set({ size_mm: numOrNull(e.target.value) })} />
          </label>
          <label>
            Label <input value={d.label ?? ""} maxLength={40} onChange={(e) => set({ label: e.target.value === "" ? null : e.target.value })} />
          </label>
        </>
      )}
      {role === "car" && (
        <>
          <label>
            Car{" "}
            <select value={d.car ?? ""} onChange={(e) => set({ car: e.target.value })}>
              {config.cars.map((c) => (
                <option key={c.name}>{c.name}</option>
              ))}
            </select>
          </label>
          <label>
            Tag offset forward/left (mm){" "}
            <input type="number" className="short" value={d.offset_mm?.[0] ?? ""} placeholder="not measured"
              onChange={(e) => set({ offset_mm: [numOrNull(e.target.value) ?? 0, d.offset_mm?.[1] ?? 0] })} />
            <input type="number" className="short" value={d.offset_mm?.[1] ?? ""} placeholder="not measured"
              onChange={(e) => set({ offset_mm: [d.offset_mm?.[0] ?? 0, numOrNull(e.target.value) ?? 0] })} />
          </label>
          <label>
            Heading offset (°) <input type="number" className="short" value={d.heading_offset_deg ?? ""} placeholder="not measured"
              onChange={(e) => set({ heading_offset_deg: numOrNull(e.target.value) })} />
          </label>
        </>
      )}
      {(role === "node" || role === "anchor") && (
        <>
          <label>
            <input type="checkbox" checked={d.origin ?? false} onChange={(e) => set({ origin: e.target.checked, pose: e.target.checked ? null : d.pose })} /> Origin (defines the floor frame)
          </label>
          {!d.origin && (
            <label>
              Typed-in pose x / y / yaw (empty = locate automatically){" "}
              <input type="number" className="short" value={pose?.x_mm ?? ""} onChange={(e) => setPose("x_mm", e.target.value)} />
              <input type="number" className="short" value={pose?.y_mm ?? ""} onChange={(e) => setPose("y_mm", e.target.value)} />
              <input type="number" className="short" value={pose?.yaw_deg ?? ""} onChange={(e) => setPose("yaw_deg", e.target.value)} />
            </label>
          )}
        </>
      )}
      {role === "node" && (
        <>
          <label>
            Row / column (empty = not part of the road network){" "}
            <input type="number" className="short" value={d.grid?.[0] ?? ""} min={0}
              onChange={(e) => { const r = intOrNull(e.target.value); set({ grid: r === null ? null : [r, d.grid?.[1] ?? 0] }); }} />
            <input type="number" className="short" value={d.grid?.[1] ?? ""} min={0}
              onChange={(e) => { const c = intOrNull(e.target.value); set({ grid: c === null ? null : [d.grid?.[0] ?? 0, c] }); }} />
          </label>
          <label>
            Station{" "}
            <input value={d.station?.name ?? ""} placeholder="name (empty = none)" maxLength={24}
              onChange={(e) => set({ station: e.target.value === "" ? null : { name: e.target.value, kind: d.station?.kind ?? "custom" } })} />
            <select value={d.station?.kind ?? "custom"} disabled={!d.station}
              onChange={(e) => set({ station: d.station ? { ...d.station, kind: e.target.value as StationKind } : null })}>
              {config.station_kinds.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </label>
        </>
      )}
      {role === "obstacle" && (
        <label>
          Blocking radius (mm, empty = {config.layout_rules.tracking["obstacle_block_radius_mm"]}){" "}
          <input type="number" value={d.radius_mm ?? ""} onChange={(e) => set({ radius_mm: numOrNull(e.target.value) })} />
        </label>
      )}
      <div className="buttons">
        <button onClick={save}>Save</button>
        <button onClick={onCancel}>Cancel</button>
        <span className="muted">version {entry.version}</span>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
