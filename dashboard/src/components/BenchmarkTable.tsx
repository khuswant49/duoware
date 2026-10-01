import { useCallback, useEffect, useState } from "react";
import { apiGet, apiSend, describeError } from "../api/client";
import type { BenchRun } from "../api/types";
import { applyPatch, benchRows, type BenchRow } from "./bench";
import { wallTime } from "./fmt";

/** Benchmark runs of one camera (PROTOCOL.md §4.7), newest first; "Apply" makes a row's settings the camera
 * overrides (PUT /api/camera-settings). */
export function BenchmarkTable({ cam }: { cam: number }) {
  const [runs, setRuns] = useState<BenchRun[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRuns((await apiGet<{ runs: BenchRun[] }>(`/api/cameras/${cam}/benchmarks`)).runs);
    } catch (e) {
      setMsg(describeError(e));
    }
  }, [cam]);

  useEffect(() => {
    void load();
  }, [load]);

  const apply = async (row: BenchRow) => {
    const patch = applyPatch(row.result);
    if (!window.confirm(`Use these camera settings?\n${JSON.stringify(patch)}`)) return;
    try {
      await apiSend("PUT", "/api/camera-settings", patch);
      setMsg(`Applied: ${JSON.stringify(patch)}`);
    } catch (e) {
      setMsg(describeError(e));
    }
  };

  const rows = runs ? benchRows(runs) : [];
  return (
    <section className="panel">
      <h3>
        Benchmarks, camera {cam} <button onClick={() => void load()}>Refresh</button>
      </h3>
      {msg && <p className="muted">{msg}</p>}
      {runs !== null && rows.length === 0 && <p className="muted">No benchmark runs yet (start one in the phone app).</p>}
      {rows.length > 0 && (
        <div className="table-scroll">
          <table className="bench">
            <thead>
              <tr>
                <th>Run</th>
                <th>Resolution</th>
                <th>fps (got / target)</th>
                <th>Pipeline</th>
                <th>ArUco 3</th>
                <th>Full scan every</th>
                <th>Detect full p50/p95 ms</th>
                <th>Detect ROI p50/p95 ms</th>
                <th>Capture→sent p50/p95 ms</th>
                <th>CPU</th>
                <th>Headroom</th>
                <th>Markers</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <td title={r.runId}>{wallTime(r.receivedMs)}</td>
                  <td>{r.resolution}</td>
                  <td>{r.fps}</td>
                  <td>{r.pipeline}</td>
                  <td>{r.aruco3}</td>
                  <td>{r.fullScanEvery}</td>
                  <td>{r.detectFull}</td>
                  <td>{r.detectRoi}</td>
                  <td>{r.capToSent}</td>
                  <td>{r.cpu}</td>
                  <td>{r.headroom}</td>
                  <td>{r.markers}</td>
                  <td>{r.applicable ? <button onClick={() => void apply(r)}>Apply</button> : <span className="muted">baseline</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
