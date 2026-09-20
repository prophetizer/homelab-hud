// SPDX-License-Identifier: Apache-2.0
import type { BoardSummary, Placement, ResolvedBoard } from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

/** Fired on any 401 so the session hook can drop to the login screen at once. */
export const UNAUTHORIZED_EVENT = "hud:unauthorized";

let csrfToken: string | null = null;

/** The session's CSRF token, from `GET /auth/me`; every mutating call echoes it. */
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

async function raise(res: Response): Promise<never> {
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    // not JSON; keep the status line
  }
  if (res.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  throw new ApiError(res.status, detail);
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal: signal ?? null, cache: "no-store" });
  if (!res.ok) return raise(res);
  return (await res.json()) as T;
}

export async function sendJson<T>(method: "POST" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? null : JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) return raise(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function fetchBoards(signal?: AbortSignal): Promise<{ boards: BoardSummary[] }> {
  return getJson("/api/v1/boards", signal);
}

export function fetchBoard(name: string, signal?: AbortSignal): Promise<ResolvedBoard> {
  return getJson(`/api/v1/boards/${encodeURIComponent(name)}`, signal);
}

/** The layout editor's save. 409 means the file changed underneath; reload and retry. */
export function patchBoard(name: string, revision: string, widgets: Placement[]): Promise<ResolvedBoard> {
  return sendJson("PATCH", `/api/v1/boards/${encodeURIComponent(name)}`, { revision, widgets });
}
