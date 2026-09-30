import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiGet, apiSend, describeError, parseApiError } from "./client";

afterEach(() => vi.unstubAllGlobals());

const reply = (status: number, body: string) => ({ ok: status < 400, status, text: async () => body }) as Response;

describe("parseApiError", () => {
  it("reads the PROTOCOL.md section 7.1 body", () => {
    const e = parseApiError(409, JSON.stringify({ error: { code: "version_conflict", message: "changed", details: { current: { version: 3 } } } }));
    expect(e).toBeInstanceOf(ApiError);
    expect([e.code, e.message, e.status]).toEqual(["version_conflict", "changed", 409]);
    expect(e.details).toEqual({ current: { version: 3 } });
  });

  it("falls back to http_error for anything else", () => {
    expect(parseApiError(502, "Bad gateway").code).toBe("http_error");
    expect(parseApiError(500, "").message).toBe("HTTP 500");
    expect(parseApiError(400, JSON.stringify({ hello: 1 })).code).toBe("http_error");
    expect(parseApiError(400, JSON.stringify({ error: { message: "no code" } })).code).toBe("http_error");
  });

  it("tolerates a missing message or details", () => {
    const e = parseApiError(403, JSON.stringify({ error: { code: "remote_forbidden" } }));
    expect([e.code, e.message, e.details]).toEqual(["remote_forbidden", "remote_forbidden", {}]);
  });
});

describe("apiGet / apiSend", () => {
  it("returns the parsed body and sends JSON with the right method", async () => {
    const f = vi.fn().mockResolvedValue(reply(200, '{"estop":true}'));
    vi.stubGlobal("fetch", f);
    expect(await apiSend("POST", "/api/control/stop_all", {})).toEqual({ estop: true });
    expect(f).toHaveBeenCalledWith("/api/control/stop_all", expect.objectContaining({ method: "POST", body: "{}", headers: { "Content-Type": "application/json" } }));
    await apiGet("/api/tags");
    expect(f.mock.calls[1][1]).toMatchObject({ method: "GET", body: undefined, headers: undefined });
  });

  it("handles an empty body (204)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(204, "")));
    expect(await apiSend("DELETE", "/api/venue-presets/x")).toBeUndefined();
  });

  it("throws an ApiError with code, message, details and status", async () => {
    const body = JSON.stringify({ error: { code: "nodes_not_visible", message: "Node(s) [6] are not seen", details: { ids: [6] } } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(409, body)));
    const err = await apiSend("POST", "/api/layout/measure", {}).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const e = err as ApiError;
    expect([e.code, e.status, e.details]).toEqual(["nodes_not_visible", 409, { ids: [6] }]);
    expect(describeError(e)).toBe("nodes_not_visible: Node(s) [6] are not seen");
  });

  it("turns a network failure into status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const e = (await apiGet("/api/tags").catch((x: unknown) => x)) as ApiError;
    expect([e.code, e.status, e.message]).toEqual(["network", 0, "Failed to fetch"]);
    expect(describeError(new Error("plain"))).toBe("plain");
  });
});
