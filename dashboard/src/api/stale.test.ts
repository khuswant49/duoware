import { describe, expect, it } from "vitest";
import { isStale, secondsSince } from "./stale";

describe("isStale", () => {
  it("is live while snapshots keep arriving", () => {
    expect(isStale(10_000, 9_950, true, 20)).toBe(false);
    expect(isStale(10_000, 9_100, true, 20)).toBe(false); // 900 ms: still inside the 1 s minimum
  });

  it("is stale after max(1 s, 3 periods) without a snapshot", () => {
    expect(isStale(10_000, 8_900, true, 20)).toBe(true);
    expect(isStale(10_000, 9_000, true, 20)).toBe(false); // exactly 1 s is not more than 1 s
    expect(isStale(20_000, 16_000, true, 1)).toBe(true); // a slow server (1 Hz): 3 periods = 3 s
    expect(isStale(20_000, 17_500, true, 1)).toBe(false);
  });

  it("is stale when the socket is closed, however fresh the last snapshot", () => {
    expect(isStale(10_000, 9_999, false, 20)).toBe(true);
  });

  it("is stale before the first snapshot", () => {
    expect(isStale(10_000, null, true, 20)).toBe(true);
  });

  it("copes with a nonsense rate", () => {
    expect(isStale(10_000, 6_000, true, 0)).toBe(true); // no rate known: a 1 s period, so 3 s
    expect(isStale(10_000, 8_000, true, 0)).toBe(false);
  });
});

describe("secondsSince", () => {
  it("counts whole seconds and never goes negative", () => {
    expect(secondsSince(15_900, 10_000)).toBe(5);
    expect(secondsSince(9_000, 10_000)).toBe(0);
    expect(secondsSince(10_000, null)).toBeNull();
  });
});
