import { Fragment, useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, describeError } from "../api/client";
import { bodyOf, withGrid } from "../api/tagbody";
import type { ApplyResult, BatchChange, ConfigSummary, Layout, StateMsg, Suggestion, TagEntry, TagList, VenuePreset } from "../api/types";
import { NOT_MEASURED, num } from "../components/fmt";
import { TagEditor } from "./TagEditor";

interface Props {
  state: StateMsg | null;
}

interface Row {
  id: number;
  entry: TagEntry | null;
}

export function TagsPage({ state }: Props) {
  const [list, setList] = useState<TagList | null>(null);
  const [config, setConfig] = useState<ConfigSummary | null>(null);
  const [presets, setPresets] = useState<VenuePreset[]>([]);
  const [editing, setEditing] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [applied, setApplied] = useState<ApplyResult | null>(null);
  const [saveName, setSaveName] = useState("");
  const [saveDesc, setSaveDesc] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const registryVersion = state?.system.registry_version;

  const reload = useCallback(() => {
    apiGet<TagList>("/api/tags").then(setList).catch((e: unknown) => setError(describeError(e)));
    apiGet<VenuePreset[]>("/api/venue-presets").then(setPresets).catch(() => undefined);
  }, []);

  useEffect(() => {
    apiGet<ConfigSummary>("/api/config").then(setConfig).catch((e: unknown) => setError(describeError(e)));
  }, []);
  useEffect(reload, [reload, registryVersion]);

  const guard = async (fn: () => Promise<string | void>) => {
    setError(null);
    setMessage(null);
    try {
      const m = await fn();
      if (m) setMessage(m);
      reload();
    } catch (e) {
      setError(describeError(e));
    }
  };

  const live = new Map((state?.tags ?? []).map((t) => [t.id, t]));
  const byId = new Map((list?.tags ?? []).map((t) => [t.id, t]));
  const ids = [...new Set([...byId.keys(), ...live.keys()])].sort((a, b) => a - b);
  const rows: Row[] = ids.map((id) => ({ id, entry: byId.get(id) ?? null }));

  const suggest = () =>
    guard(async () => {
      setSuggestion(await apiSend<Suggestion>("POST", "/api/layout/suggest", {}));
    });

  const applySuggestion = () =>
    guard(async () => {
      if (!suggestion) return;
      const changes: BatchChange[] = [];
      for (const s of suggestion.suggestions) {
        const e = byId.get(s.id);
        if (e && e.role === "node") changes.push({ id: s.id, doc: withGrid(e, s.grid), expected_version: e.version });
      }
      await apiSend("POST", "/api/tags/batch", { changes });
      setSuggestion(null);
      return `Applied rows and columns to ${changes.length} nodes.`;
    });

  const measure = () =>
    guard(async () => {
      const cur = await apiGet<Layout>("/api/layout");
      const l = await apiSend<Layout>("POST", "/api/layout/measure", { expected_layout_version: cur.version });
      return `Measured ${l.nodes.length} nodes; grid rotation ${l.grid_rotation_deg.toFixed(1)}°; ${l.warnings.length} warning(s).`;
    });

  const applyPreset = (p: VenuePreset) => {
    if (!window.confirm(`Apply "${p.name}"? This replaces the tag roles${p.has_layout ? " and the measured layout" : ""}.`)) return;
    void guard(async () => {
      const r = await apiSend<ApplyResult>("POST", `/api/venue-presets/${encodeURIComponent(p.name)}/apply`, { expected_registry_version: list?.registry_version });
      setApplied(r);
      return `Applied "${p.name}".`;
    });
  };

  const deletePreset = (p: VenuePreset) => {
    if (window.confirm(`Delete the venue preset "${p.name}"?`)) void guard(async () => { await apiSend("DELETE", `/api/venue-presets/${encodeURIComponent(p.name)}`); return `Deleted "${p.name}".`; });
  };

  const savePreset = () =>
    guard(async () => {
      await apiSend("POST", "/api/venue-presets", { name: saveName, description: saveDesc, overwrite });
      return `Saved "${saveName}".`;
    });

  const sizeText = (r: Row) => {
    const t = live.get(r.id);
    const printed = r.entry?.size_mm;
    if (!t || t.size_mm === null) return printed ? `${printed} mm printed, ${NOT_MEASURED}` : NOT_MEASURED;
    return `${printed ?? "?"} printed / ${t.size_mm.toFixed(1)} measured`;
  };

  return (
    <div className="tags-page">
      <div className="toolbar">
        <button onClick={suggest}>Suggest rows/columns</button>
        <button onClick={measure}>Measure layout</button>
        {message && <span className="ok-text">{message}</span>}
        {error && <span className="error">{error}</span>}
      </div>

      {suggestion && (
        <section className="panel">
          <h3>Suggested rows and columns</h3>
          <p className="muted">Grid direction {suggestion.grid_rotation_deg.toFixed(1)}°, spacing about {suggestion.spacing_mm.toFixed(0)} mm. Nothing is saved until you apply.</p>
          <table>
            <thead><tr><th>Tag</th><th>Now</th><th>Suggested</th></tr></thead>
            <tbody>
              {suggestion.suggestions.map((s) => (
                <tr key={s.id}>
                  <td>{s.id}</td>
                  <td>{byId.get(s.id)?.grid ? byId.get(s.id)?.grid?.join(", ") : "-"}</td>
                  <td>{s.grid.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {suggestion.conflicts.map((c, i) => (
            <p key={i} className="warning">Tags {c.ids.join(" and ")} both fall on row {c.grid[0]}, column {c.grid[1]}: no suggestion for them.</p>
          ))}
          <button onClick={applySuggestion}>Apply</button> <button onClick={() => setSuggestion(null)}>Discard</button>
        </section>
      )}

      <table className="tags">
        <thead>
          <tr><th>ID</th><th>Role</th><th>Seen</th><th>x / y (mm)</th><th>Size</th><th>Row, col</th><th>Label</th><th>Version</th><th /></tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const t = live.get(r.id);
            const role = r.entry?.role ?? t?.role ?? "unassigned";
            const sizeWarn = t?.size_warn ?? false;
            return (
              <Fragment key={r.id}>
                <tr className={role === "unassigned" ? "unassigned-row" : undefined}>
                  <td>{r.id}</td>
                  <td>{role}{r.entry?.car ? ` (${r.entry.car})` : ""}{r.entry?.origin ? " · origin" : ""}{r.entry?.station ? ` · ${r.entry.station.name}` : ""}</td>
                  <td>{t?.seen ? "yes" : "no"}</td>
                  <td>{t && t.x_mm !== null ? `${num(t.x_mm, "", 0)} / ${num(t.y_mm, "", 0)}` : NOT_MEASURED}</td>
                  <td className={sizeWarn ? "bad-text" : undefined}>{sizeText(r)}{sizeWarn ? " ⚠ differs from the printed size" : ""}</td>
                  <td>{r.entry?.grid ? r.entry.grid.join(", ") : "-"}</td>
                  <td>{r.entry?.label ?? ""}</td>
                  <td>{r.entry?.version ?? 0}</td>
                  <td><button onClick={() => setEditing(editing === r.id ? null : r.id)}>{editing === r.id ? "Close" : "Edit"}</button></td>
                </tr>
                {editing === r.id && config && (
                  <tr>
                    <td colSpan={9}>
                      <TagEditor
                        entry={{ id: r.id, version: r.entry?.version ?? 0, body: r.entry && r.entry.role !== "unassigned" ? bodyOf(r.entry) : null }}
                        config={config}
                        onCancel={() => setEditing(null)}
                        onDone={(m) => { setEditing(null); setMessage(m); reload(); }}
                      />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      {rows.length === 0 && <p className="muted">No tags yet. Apply a venue preset below, or show a tag to the camera.</p>}

      <section className="panel">
        <h3>Venue presets</h3>
        <table>
          <thead><tr><th>Name</th><th>Description</th><th>Tags</th><th>Layout</th><th>Camera</th><th /></tr></thead>
          <tbody>
            {presets.map((p) => (
              <tr key={p.name}>
                <td>{p.name}{p.builtin ? " (built in)" : ""}</td>
                <td>{p.description}</td>
                <td>{p.tag_count}</td>
                <td>{p.has_layout ? "yes" : "no"}</td>
                <td>{p.has_camera ? "yes" : "no"}</td>
                <td>
                  <button onClick={() => applyPreset(p)}>Apply</button>{" "}
                  <button disabled={p.builtin} onClick={() => deletePreset(p)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {applied && (
          <p>
            Check against the camera: {applied.check.matched.length} matched, {applied.check.moved.length} moved
            {applied.check.moved.length > 0 && ` (${applied.check.moved.map((m) => `${m.id}: ${m.distance_mm.toFixed(0)} mm`).join(", ")})`},{" "}
            {applied.check.missing.length} not seen{applied.check.missing.length > 0 && ` (${applied.check.missing.join(", ")})`}.
          </p>
        )}
        <div className="toolbar">
          <input placeholder="Save the current setup as…" value={saveName} onChange={(e) => setSaveName(e.target.value)} />
          <input placeholder="Description" value={saveDesc} onChange={(e) => setSaveDesc(e.target.value)} />
          <label><input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} /> overwrite</label>
          <button disabled={saveName.trim() === ""} onClick={savePreset}>Save as</button>
        </div>
      </section>
    </div>
  );
}
