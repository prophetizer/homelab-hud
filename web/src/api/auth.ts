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
  session_expires?: number | null; // epoch seconds; null when the proxy signs you in
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

// ----------------------------------------------------------------------------- account

export interface Account {
  subject: string;
  display_name: string;
  source: Source;
  groups: string[];
  can_change_password: boolean;
  session_expires: number | null; // epoch seconds; null when the proxy signs you in
  sessions: number | null;
  min_password_length: number;
}

export function fetchAccount(signal?: AbortSignal): Promise<Account> {
  return getJson("/api/v1/auth/account", signal);
}

/** Ends every session of yours, this one too: sign in again afterwards. */
export function changePassword(current_password: string, new_password: string): Promise<void> {
  return sendJson("POST", "/api/v1/auth/password", { current_password, new_password });
}

export function logoutEverywhere(): Promise<void> {
  return sendJson("POST", "/api/v1/auth/logout-all");
}

/** How you sign in, in words. */
export function sourceWords(source: Source): string {
  if (source === "local") return "a HUD account (username and password kept by HUD)";
  if (source === "oidc") return "single sign-on (your identity provider holds the password)";
  return "your reverse proxy's login (the proxy holds the password)";
}

// ----------------------------------------------------------------------------- users

export interface UserOut {
  id: number;
  subject: string;
  display_name: string;
  source: Source;
  groups: string[];
  created_at: number;
  last_seen: number | null;
}

export interface GroupInfo {
  name: string;
  admin: boolean;
  permissions: string[];
}

export function fetchUsers(signal?: AbortSignal): Promise<{ users: UserOut[] }> {
  return getJson("/api/v1/auth/users", signal);
}

export function fetchGroups(signal?: AbortSignal): Promise<{ groups: GroupInfo[]; unmatched_group: string }> {
  return getJson("/api/v1/auth/groups", signal);
}

export function createUser(body: {
  username: string;
  password: string;
  display_name: string | null;
  groups: string[];
}): Promise<UserOut> {
  return sendJson("POST", "/api/v1/auth/users", body);
}

export function updateUser(id: number, body: { groups?: string[]; password?: string }): Promise<UserOut> {
  return sendJson("PATCH", `/api/v1/auth/users/${id}`, body);
}

export function deleteUser(id: number): Promise<void> {
  return sendJson("DELETE", `/api/v1/auth/users/${id}`);
}
