// Pure map geometry. Floor frame (PROTOCOL.md §0): mm, +x right, +y down, degrees clockwise. The map is drawn in the
// GRID frame: the floor frame rotated by -grid_rotation_deg (DECISIONS.md D27), so rows run left to right. SVG
// coordinates are y down as well, so no flip is needed.

export type Pt = [number, number];

export const MARGIN = 0.1; // fraction of the drawing added around the bounds on every side

const rad = (deg: number) => (deg * Math.PI) / 180;

/** Floor mm -> grid frame mm (rotate by -rotationDeg). */
export function floorToGrid([x, y]: Pt, rotationDeg: number): Pt {
  const c = Math.cos(rad(rotationDeg));
  const s = Math.sin(rad(rotationDeg));
  return [x * c + y * s, -x * s + y * c];
}

/** Grid frame mm -> floor mm. */
export function gridToFloor([x, y]: Pt, rotationDeg: number): Pt {
  const c = Math.cos(rad(rotationDeg));
  const s = Math.sin(rad(rotationDeg));
  return [x * c - y * s, x * s + y * c];
}

/** A heading in the floor frame as seen in the grid frame (PROTOCOL.md §0: heading_grid = heading_floor - rotation). */
export function headingInGrid(headingFloorDeg: number, rotationDeg: number): number {
  let h = (headingFloorDeg - rotationDeg) % 360;
  if (h <= -180) h += 360;
  if (h > 180) h -= 360;
  return h;
}

export interface MapTransform {
  rotationDeg: number;
  scale: number; // SVG units per mm
  toSvg(p: Pt): Pt; // floor mm -> SVG
  toFloor(p: Pt): Pt; // SVG -> floor mm
  lengthToSvg(mm: number): number;
}

/** Fits `floorPoints` (floor mm) into a `width` x `height` SVG with a 10 % margin, keeping the aspect ratio. */
export function makeTransform(floorPoints: Pt[], rotationDeg: number, width: number, height: number): MapTransform {
  const g = floorPoints.map((p) => floorToGrid(p, rotationDeg));
  let minX = 0;
  let maxX = 1000;
  let minY = 0;
  let maxY = 600;
  if (g.length > 0) {
    minX = Math.min(...g.map((p) => p[0]));
    maxX = Math.max(...g.map((p) => p[0]));
    minY = Math.min(...g.map((p) => p[1]));
    maxY = Math.max(...g.map((p) => p[1]));
  }
  const w = Math.max(maxX - minX, 1);
  const h = Math.max(maxY - minY, 1);
  const scale = Math.min(width / (w * (1 + 2 * MARGIN)), height / (h * (1 + 2 * MARGIN)));
  const offX = width / 2 - ((minX + maxX) / 2) * scale; // the middle of the bounds lands in the middle of the view
  const offY = height / 2 - ((minY + maxY) / 2) * scale;
  return {
    rotationDeg,
    scale,
    toSvg: (p) => {
      const [gx, gy] = floorToGrid(p, rotationDeg);
      return [gx * scale + offX, gy * scale + offY];
    },
    toFloor: ([sx, sy]) => gridToFloor([(sx - offX) / scale, (sy - offY) / scale], rotationDeg),
    lengthToSvg: (mm) => mm * scale,
  };
}
