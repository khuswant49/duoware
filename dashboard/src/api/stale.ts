// Is the state on screen still live? Pure, so it can be tested without a browser (M1 review F2).

export const STALE_MIN_MS = 1000; // never call the state stale before this
export const STALE_PERIODS = 3; // ... or before this many snapshot periods without one

/** Not connected, or no `state` snapshot for more than max(1 s, 3 periods of `stateHz`). */
export function isStale(nowMs: number, lastStateMs: number | null, connected: boolean, stateHz: number): boolean {
  if (!connected || lastStateMs === null) return true;
  const period = stateHz > 0 ? 1000 / stateHz : STALE_MIN_MS;
  return nowMs - lastStateMs > Math.max(STALE_MIN_MS, STALE_PERIODS * period);
}

/** Whole seconds since the last snapshot, for the banner ("No live data for 12 s"). */
export function secondsSince(nowMs: number, lastStateMs: number | null): number | null {
  return lastStateMs === null ? null : Math.max(0, Math.floor((nowMs - lastStateMs) / 1000));
}
