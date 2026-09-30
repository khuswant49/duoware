import type { ApiErrorBody } from "./types";

/** An error answer from the server in the PROTOCOL.md §7.1 shape (or a network failure, status 0). */
export class ApiError extends Error {
  code: string;
  details: Record<string, unknown>;
  status: number;

  constructor(code: string, message: string, details: Record<string, unknown>, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.details = details;
    this.status = status;
  }
}

/** Turns a failed response body into an ApiError. Anything that is not a §7.1 body becomes `http_error`. */
export function parseApiError(status: number, text: string): ApiError {
  try {
    const body = JSON.parse(text) as Partial<ApiErrorBody>;
    const e = body.error;
    if (e && typeof e.code === "string") {
      return new ApiError(e.code, typeof e.message === "string" ? e.message : e.code, e.details ?? {}, status);
    }
  } catch {
    // not JSON: fall through
  }
  return new ApiError("http_error", text.trim().slice(0, 200) || `HTTP ${status}`, {}, status);
}

export type Method = "GET" | "POST" | "PUT" | "DELETE";

export async function apiSend<T>(method: Method, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let r: Response;
  try {
    r = await fetch(path, {
      method,
      signal,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError("network", e instanceof Error ? e.message : String(e), {}, 0);
  }
  const text = await r.text();
  if (!r.ok) throw parseApiError(r.status, text);
  return (text ? JSON.parse(text) : undefined) as T;
}

export function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return apiSend<T>("GET", path, undefined, signal);
}

/** "version_conflict: Tag 5 was changed..." for display next to a form. */
export function describeError(e: unknown): string {
  if (e instanceof ApiError) return `${e.code}: ${e.message}`;
  return e instanceof Error ? e.message : String(e);
}
