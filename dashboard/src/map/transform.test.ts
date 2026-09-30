import { describe, expect, it } from "vitest";
import { floorToGrid, gridToFloor, headingInGrid, makeTransform, MARGIN, type Pt } from "./transform";

const close = (a: Pt, b: Pt, digits = 6) => {
  expect(a[0]).toBeCloseTo(b[0], digits);
  expect(a[1]).toBeCloseTo(b[1], digits);
};

describe("floor <-> grid frame", () => {
  it("round trips for any rotation", () => {
    for (const rot of [-170, -45, 0, 8, 90, 179]) {
      for (const p of [[0, 0], [100, -40], [-333.3, 812.5]] as Pt[]) close(gridToFloor(floorToGrid(p, rot), rot), p);
    }
  });

  it("rotates the grid direction onto the x axis (rotation measured clockwise, y down)", () => {
    const rot = 8;
    const r = (rot * Math.PI) / 180;
    close(floorToGrid([Math.cos(r) * 100, Math.sin(r) * 100], rot), [100, 0]);
    close(floorToGrid([-Math.sin(r) * 50, Math.cos(r) * 50], rot), [0, 50]); // a column direction
  });

  it("subtracts the rotation from a heading and wraps to (-180, 180]", () => {
    expect(headingInGrid(8, 8)).toBeCloseTo(0);
    expect(headingInGrid(-175, 10)).toBeCloseTo(175);
    expect(headingInGrid(180, 0)).toBeCloseTo(180);
    expect(headingInGrid(-90, 8)).toBeCloseTo(-98);
  });
});

describe("makeTransform", () => {
  const pts: Pt[] = [[0, 0], [800, 100], [-100, 900], [700, 1000]];

  it("fits every point inside the view and round trips", () => {
    for (const rot of [0, 8, -30]) {
      const tf = makeTransform(pts, rot, 900, 560);
      for (const p of pts) {
        const [sx, sy] = tf.toSvg(p);
        expect(sx).toBeGreaterThanOrEqual(0);
        expect(sx).toBeLessThanOrEqual(900);
        expect(sy).toBeGreaterThanOrEqual(0);
        expect(sy).toBeLessThanOrEqual(560);
        close(tf.toFloor([sx, sy]), p, 6);
      }
    }
  });

  it("leaves the margin around the drawing and centres it", () => {
    const tf = makeTransform([[0, 0], [1000, 0], [0, 500], [1000, 500]], 0, 1200, 800);
    const xs = [0, 1000].map((x) => tf.toSvg([x, 0])[0]);
    expect(xs[0]).toBeCloseTo((1200 - 1000 * tf.scale) / 2);
    expect(xs[0] / (1000 * tf.scale)).toBeGreaterThanOrEqual(MARGIN - 1e-9);
    const ys = [0, 500].map((y) => tf.toSvg([0, y])[1]);
    expect(ys[0] + ys[1]).toBeCloseTo(800);
  });

  it("keeps the aspect ratio and y pointing down", () => {
    const tf = makeTransform(pts, 0, 900, 560);
    const [ax, ay] = tf.toSvg([0, 0]);
    const [bx, by] = tf.toSvg([100, 100]);
    expect(bx - ax).toBeCloseTo(by - ay);
    expect(by).toBeGreaterThan(ay);
    expect(tf.lengthToSvg(100)).toBeCloseTo(bx - ax);
  });

  it("draws the grid direction horizontally", () => {
    const rot = 8;
    const tf = makeTransform([[0, 0], [454, 58]], rot, 900, 560);
    const a = tf.toSvg([0, 0]);
    const b = tf.toSvg([454, 58]); // 7.3 degrees: within a degree of the 8 degree grid direction
    expect(Math.abs(b[1] - a[1])).toBeLessThan(Math.abs(b[0] - a[0]) * 0.02);
  });

  it("copes with no points and with a single point", () => {
    expect(Number.isFinite(makeTransform([], 0, 900, 560).scale)).toBe(true);
    const one = makeTransform([[5, 5]], 12, 900, 560);
    close(one.toSvg([5, 5]), [450, 280], 6);
  });
});
