import type { Role, TagBody, TagEntry } from "./types";

/** The writable document of a tag entry (what PUT /api/tags/{id} takes), without the live fields. */
export function bodyOf(e: TagEntry): TagBody {
  if (e.role === "unassigned") throw new Error("an unassigned tag has no document");
  const b: TagBody = { role: e.role, size_mm: e.size_mm, label: e.label };
  switch (e.role) {
    case "car":
      b.car = e.car;
      b.offset_mm = e.offset_mm ?? null;
      b.heading_offset_deg = e.heading_offset_deg ?? null;
      break;
    case "node":
    case "anchor":
      b.origin = e.origin ?? false;
      b.pose = e.pose ?? null;
      if (e.role === "node") {
        b.grid = e.grid ?? null;
        b.station = e.station ?? null;
      }
      break;
    case "obstacle":
      b.radius_mm = e.radius_mm ?? null;
      break;
    case "ignore":
      break;
  }
  return b;
}

/** A blank document for a newly chosen role. Sizes default to the configured printed size. */
export function blankBody(role: Role, defaultSizeMm: number, carName?: string): TagBody {
  const b: TagBody = { role, size_mm: defaultSizeMm, label: null };
  if (role === "car") Object.assign(b, { car: carName ?? "", offset_mm: null, heading_offset_deg: null });
  if (role === "node") Object.assign(b, { origin: false, pose: null, grid: null, station: null });
  if (role === "anchor") Object.assign(b, { origin: false, pose: null });
  if (role === "obstacle") b.radius_mm = null;
  return b;
}

/** Same document with the row and column replaced (used by "Suggest rows/columns" -> Apply). */
export function withGrid(e: TagEntry, grid: number[]): TagBody {
  return { ...bodyOf(e), grid };
}
