// SPDX-License-Identifier: Apache-2.0
// Mirrors hud/api/auth.py. Kept in step by hand.
import { getJson, sendJson } from "./client";

export type Source = "local" | "forward" | "oidc";

export interface Me {
  subject: string;
  display_name: string;
  groups: string[];
  source: Source;
  permissions: string[];
  csrf_token: string;
}

export interface Backends {
  backends: Source[];
  setup_required: boolean;
  registration: boolean;
}

export function fetchBackends(signal?: AbortSignal): Promise<Backends> {
  return getJson("/api/v1/auth/backends", signal);
}

export function fetchMe(signal?: AbortSignal): Promise<Me> {
  return getJson("/api/v1/auth/me", signal);
}

export function login(username: string, password: string): Promise<Me> {
  return sendJson("POST", "/api/v1/auth/login", { username, password });
}

export function setup(username: string, password: string, display_name: string): Promise<Me> {
  return sendJson("POST", "/api/v1/auth/setup", { username, password, display_name: display_name || null });
}

export function register(username: string, password: string, display_name: string): Promise<Me> {
  return sendJson("POST", "/api/v1/auth/register", { username, password, display_name: display_name || null });
}

export function logout(): Promise<void> {
  return sendJson("POST", "/api/v1/auth/logout");
}

export function oidcStartUrl(next: string): string {
  return `/api/v1/auth/oidc/start?next=${encodeURIComponent(next)}`;
}

/** `*` or a matching wildcard grants; mirrors hud/auth/principal.py. */
export function hasPermission(me: Me | null, wanted: string): boolean {
  if (!me) return false;
  return me.permissions.some((g) => {
    if (g === "*") return true;
    if (g.endsWith(":*")) return wanted === g.slice(0, -2) || wanted.startsWith(g.slice(0, -1));
    return g === wanted;
  });
}
