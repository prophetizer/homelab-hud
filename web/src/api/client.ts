// SPDX-License-Identifier: Apache-2.0
import type { App, BoardSummary, Placement, ResolvedBoard, ResolvedWidget } from "./types";

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

/** HUD marks every response it sends (hud/api/marker.py); a login gate in front of HUD
 *  (Authelia via Traefik) does not. */
export const HUD_HEADER = "X-HUD";
const RELOAD_KEY = "hud.gateReloadAt";

/**
 * True when the gate in front of HUD answered instead of HUD: its session lapsed, so it
 * redirected the call to its sign-in portal or refused it. HUD's own 401 means "sign in
 * to HUD" and is handled by the session hook; this one means "sign in at the gate".
 */
export function fromGate(res: Pick<Response, "type" | "status" | "headers">): boolean {
  if (res.type === "opaqueredirect") return true; // HUD's API never redirects a fetch
  return (res.status === 401 || res.status === 403) && !res.headers.has(HUD_HEADER);
}

/** Reload so the browser itself meets the gate and lands on its sign-in, then back here
 *  — at most once a minute, so a gate that refuses even page loads cannot loop us. */
function reloadThroughGate(): void {
  try {
    const last = Number(sessionStorage.getItem(RELOAD_KEY) ?? 0);
    if (Date.now() - last < 60_000) return;
    sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
  } catch {
    // storage blocked: reload anyway, once per page
  }
  window.location.reload();
}

/** The gate's answer, if it was one: reloads and throws; else returns ``res``. */
export function checkGate(res: Response): Response {
  if (fromGate(res)) {
    reloadThroughGate();
    throw new ApiError(res.status, "signed out at the login in front of HUD; reloading");
  }
  return res;
}

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
  const res = checkGate(await fetch(path, { signal: signal ?? null, cache: "no-store", redirect: "manual" }));
  if (!res.ok) return raise(res);
  return (await res.json()) as T;
}

export async function sendJson<T>(method: "POST" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const res = checkGate(
    await fetch(path, {
      method,
      headers,
      body: body === undefined ? null : JSON.stringify(body),
      cache: "no-store",
      redirect: "manual",
    }),
  );
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

/** One tile as its detail view shows it: every row, a day of trend. */
export function fetchExpanded(board: string, widget: string, signal?: AbortSignal): Promise<ResolvedWidget> {
  return getJson(`/api/v1/boards/${encodeURIComponent(board)}/widgets/${encodeURIComponent(widget)}/expanded`, signal);
}

/** One tile on its own; ``range`` shows a chart over another window. */
export function fetchWidget(board: string, widget: string, range?: string, signal?: AbortSignal): Promise<ResolvedWidget> {
  const q = range ? `?range=${encodeURIComponent(range)}` : "";
  return getJson(`/api/v1/boards/${encodeURIComponent(board)}/widgets/${encodeURIComponent(widget)}${q}`, signal);
}

/** The layout editor's save. 409 means the file changed underneath; reload and retry. */
export function patchBoard(name: string, revision: string, widgets: Placement[]): Promise<ResolvedBoard> {
  return sendJson("PATCH", `/api/v1/boards/${encodeURIComponent(name)}`, { revision, widgets });
}

export function fetchApps(signal?: AbortSignal): Promise<{ apps: App[] }> {
  return getJson("/api/v1/apps", signal);
}

export function fetchApp(board: string, widget: string, signal?: AbortSignal): Promise<App> {
  return getJson(`/api/v1/apps/${encodeURIComponent(board)}/${encodeURIComponent(widget)}`, signal);
}
