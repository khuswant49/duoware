// Display helpers. A value the system did not measure is shown as "not measured", never as 0 or a blank
// (CLAUDE.md "Honest numbers").

export const NOT_MEASURED = "not measured";

export function num(v: number | null | undefined, unit = "", digits = 1): string {
  return v === null || v === undefined ? NOT_MEASURED : `${v.toFixed(digits)}${unit ? " " + unit : ""}`;
}

export function int(v: number | null | undefined, unit = ""): string {
  return v === null || v === undefined ? NOT_MEASURED : `${v}${unit ? " " + unit : ""}`;
}

export function text(v: string | null | undefined): string {
  return v === null || v === undefined || v === "" ? NOT_MEASURED : v;
}

export function pair(p: { p50: number | null; p95: number | null } | null | undefined, unit = "ms"): string {
  if (!p || (p.p50 === null && p.p95 === null)) return NOT_MEASURED;
  return `${num(p.p50, "", 1)} / ${num(p.p95, unit, 1)}`;
}

export function wallTime(ms: number | null | undefined): string {
  return ms === null || ms === undefined ? "-" : new Date(ms).toLocaleTimeString();
}
