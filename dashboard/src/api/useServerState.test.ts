import { describe, expect, it } from "vitest";
import fixture from "./__fixtures__/state.json";
import { BACKOFF_FIRST_MS, BACKOFF_MAX_MS, backoffMs, parseServerMessage } from "./useServerState";

describe("backoffMs", () => {
  it("starts at 0.5 s, doubles, and stops at 5 s", () => {
    expect([0, 1, 2, 3, 4, 5, 20].map(backoffMs)).toEqual([500, 1000, 2000, 4000, 5000, 5000, 5000]);
    expect(backoffMs(-3)).toBe(BACKOFF_FIRST_MS);
    expect(backoffMs(1e6)).toBe(BACKOFF_MAX_MS);
  });
});

describe("parseServerMessage", () => {
  it("accepts hello, state and event messages", () => {
    expect(parseServerMessage('{"v":1,"t":"hello","server_id":"a","version":"0.0.1","mode":"sim","state_hz":20}').kind).toBe("hello");
    const { _comment, ...state } = fixture;
    void _comment;
    expect(parseServerMessage(JSON.stringify(state)).kind).toBe("state");
    const ev = parseServerMessage('{"v":1,"t":"event","event":{"id":7,"type":"registry"}}');
    expect(ev.kind === "event" && ev.event.id).toBe(7);
  });

  it("refuses another protocol version and reports it", () => {
    expect(parseServerMessage('{"v":2,"t":"state"}')).toEqual({ kind: "mismatch", v: 2 });
  });

  it("ignores junk", () => {
    for (const t of ["nope", "null", "[1]", '{"v":1,"t":"mystery"}', '"text"']) expect(parseServerMessage(t).kind).toBe("ignore");
  });
});
