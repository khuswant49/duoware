import { useEffect, useRef, useState } from "react";
import { isStale, secondsSince } from "./stale";
import { PROTOCOL_VERSION, type EventRecord, type HelloMsg, type ServerMsg, type StateMsg } from "./types";

export const BACKOFF_FIRST_MS = 500; // reconnect delay after the first failure ...
export const BACKOFF_MAX_MS = 5000; // ... doubling up to this
export const EVENTS_KEPT = 50;

/** Delay before reconnect attempt `attempt` (0 = the first): 0.5 s, 1 s, 2 s, 4 s, then 5 s. */
export function backoffMs(attempt: number): number {
  return Math.min(BACKOFF_MAX_MS, BACKOFF_FIRST_MS * 2 ** Math.max(0, attempt));
}

export type Parsed = { kind: "hello"; msg: HelloMsg } | { kind: "state"; msg: StateMsg } | { kind: "event"; event: EventRecord } | { kind: "mismatch"; v: number } | { kind: "ignore" };

/** One WebSocket text message. A message with another protocol version is reported, never used (PROTOCOL.md §0). */
export function parseServerMessage(text: string): Parsed {
  let m: ServerMsg;
  try {
    m = JSON.parse(text) as ServerMsg;
  } catch {
    return { kind: "ignore" };
  }
  if (typeof m !== "object" || m === null || typeof m.v !== "number") return { kind: "ignore" };
  if (m.v !== PROTOCOL_VERSION) return { kind: "mismatch", v: Number(m.v) };
  if (m.t === "hello") return { kind: "hello", msg: m };
  if (m.t === "state") return { kind: "state", msg: m };
  if (m.t === "event") return { kind: "event", event: m.event };
  return { kind: "ignore" };
}

export interface ServerState {
  hello: HelloMsg | null;
  state: StateMsg | null;
  connected: boolean;
  /** newest first, at most EVENTS_KEPT */
  lastEvents: EventRecord[];
  /** set when the server speaks another protocol version */
  mismatch: number | null;
  /** browser time (ms) when the latest `state` arrived */
  lastStateMs: number | null;
  /** no live data: socket closed or no snapshot for a while. The state on screen is then the LAST KNOWN one. */
  stale: boolean;
  staleForS: number | null;
}

const CLOCK_TICK_MS = 500; // how often staleness is re-evaluated

/** Live state over /ws/dashboard with reconnect and backoff (PROTOCOL.md §6). Read-only: nothing is sent. */
export function useServerState(): ServerState {
  const [s, setS] = useState({ hello: null, state: null, connected: false, lastEvents: [], mismatch: null, lastStateMs: null } as Omit<ServerState, "stale" | "staleForS">);
  const [now, setNow] = useState(() => Date.now());
  const attempt = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/dashboard`);
      ws.onopen = () => {
        attempt.current = 0;
        setS((p) => ({ ...p, connected: true }));
      };
      ws.onmessage = (ev: MessageEvent) => {
        const p = parseServerMessage(String(ev.data));
        if (p.kind === "hello") setS((x) => ({ ...x, hello: p.msg, mismatch: null }));
        else if (p.kind === "state") setS((x) => ({ ...x, state: p.msg, lastStateMs: Date.now() }));
        else if (p.kind === "event") setS((x) => ({ ...x, lastEvents: [p.event, ...x.lastEvents].slice(0, EVENTS_KEPT) }));
        else if (p.kind === "mismatch") setS((x) => ({ ...x, mismatch: p.v }));
      };
      ws.onclose = () => {
        setS((p) => ({ ...p, connected: false }));
        if (!closed) timer = setTimeout(connect, backoffMs(attempt.current++));
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    const clock = setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => {
      clearInterval(clock);
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, []);

  const hz = s.hello?.state_hz ?? 0;
  return { ...s, stale: isStale(now, s.lastStateMs, s.connected, hz), staleForS: secondsSince(now, s.lastStateMs) };
}
