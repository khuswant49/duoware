import { describe, expect, it } from "vitest";
import { protocolMatches, type Health } from "./health";

const base: Health = { status: "ok", version: "0.0.1", proto: 1, mode: "sim", uptime_s: 1 };

describe("protocolMatches", () => {
  it("accepts the same protocol major version", () => {
    expect(protocolMatches(base)).toBe(true);
  });
  it("rejects a different protocol major version", () => {
    expect(protocolMatches({ ...base, proto: 2 })).toBe(false);
  });
});
