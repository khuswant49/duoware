import { useEffect, useState } from "react";
import { apiGet, apiSend, describeError } from "../api/client";
import type { Pairing, SystemState } from "../api/types";

interface Props {
  system: SystemState | null;
  connected: boolean;
  mismatch: number | null;
}

const PAIRING_POLL_MS = 5000;

export function SystemBar({ system, connected, mismatch }: Props) {
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      apiGet<Pairing>("/api/pairing")
        .then((p) => alive && setPairing(p))
        .catch(() => undefined);
    load();
    const t = setInterval(load, PAIRING_POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(describeError(e));
    }
  };

  return (
    <header className="system-bar">
      <strong>DUO-WARE 2</strong>
      <span className={`pill ${connected ? "ok" : "bad"}`}>{connected ? "live" : "disconnected"}</span>
      {system && <span className="pill">{system.mode === "sim" ? "simulation" : "hardware"}</span>}
      {pairing && (
        <span className="pill" title="Enter this code in the phone app to pair it">
          pair code <b>{pairing.pair_code}</b>
        </span>
      )}
      {mismatch !== null && <span className="pill bad">server speaks protocol v{mismatch}: update the dashboard</span>}
      <span className="spacer" />
      {system?.estop && (
        <span className="pill bad" title={system.estop_by ? `by ${system.estop_by}` : ""}>
          E-STOP LATCHED
        </span>
      )}
      <button className="stop" onClick={() => run(() => apiSend("POST", "/api/control/stop_all", {}))}>
        STOP ALL
      </button>
      <button
        disabled={!system?.estop}
        onClick={() => {
          if (window.confirm("Resume after STOP ALL? Make sure the cars are clear.")) {
            void run(() => apiSend("POST", "/api/control/resume", { confirm: true }));
          }
        }}
      >
        RESUME
      </button>
      {error && <span className="error">{error}</span>}
    </header>
  );
}
