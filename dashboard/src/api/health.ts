// Mirrors GET /api/health in PROTOCOL.md §7.1.
export interface Health {
  status: "ok";
  version: string;
  proto: number;
  mode: "hardware" | "sim";
  uptime_s: number;
}

export const DASHBOARD_PROTOCOL_VERSION = 1;

/** A server speaking another protocol major version must not be driven by this dashboard. */
export function protocolMatches(h: Health): boolean {
  return h.proto === DASHBOARD_PROTOCOL_VERSION;
}

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const r = await fetch("/api/health", { signal });
  if (!r.ok) throw new Error(`health: HTTP ${r.status}`);
  return (await r.json()) as Health;
}
