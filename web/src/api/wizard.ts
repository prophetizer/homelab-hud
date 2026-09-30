// SPDX-License-Identifier: Apache-2.0
// Connect a service (PLAN §8.5, round 3): the bundled catalog, containers found on this
// host, a connection test, and connecting. Credentials go out once, in the request body.
import { getJson, sendJson } from "./client";

export interface WizardField {
  name: string;
  kind: "env" | "secret";
  hint: string;
  set_in: string | null; // for a secret: where it is already set ("docker secret", …)
}
export interface CatalogEntry {
  name: string;
  service: string;
  icon: string | null;
  docs: string | null;
  port: number | null;
  connected: boolean;
  fields: WizardField[];
}
export interface Found {
  container: string;
  image: string;
  template: string;
  service: string;
  connected: boolean;
  reachable: boolean | null;
  suggested_url: string | null;
  note: string | null;
}
export interface ProbeResult {
  ok: boolean;
  seconds: number;
  resources: number;
  sample: string[];
  error: string | null;
}
export interface Connection {
  template: string;
  values: Record<string, string>;
  secrets: Record<string, string>;
}

export const fetchCatalog = (signal?: AbortSignal) =>
  getJson<{ templates: CatalogEntry[] }>("/api/v1/wizard/catalog", signal);
export const fetchFound = (signal?: AbortSignal) => getJson<{ found: Found[] }>("/api/v1/wizard/discover", signal);
export const testConnection = (c: Connection) => sendJson<ProbeResult>("POST", "/api/v1/wizard/test", c);
export const connectService = (c: Connection) =>
  sendJson<{ provider: string; file: string; warnings: string[] }>("POST", "/api/v1/wizard/connect", c);

/** A base URL field's starting value: the found container's address, or a scheme. */
export function startingUrl(field: WizardField, found: Found | undefined): string {
  if (!field.name.endsWith("_URL")) return "";
  return found?.suggested_url ?? "http://";
}

/** "Found 1: Sonarr, in 0.4 s" / the error, as one line. */
export function probeText(r: ProbeResult): string {
  if (!r.ok) return r.error ?? "failed";
  const what = r.sample.length > 0 ? `: ${r.sample.join(", ")}${r.resources > r.sample.length ? ", …" : ""}` : "";
  return `Connected in ${r.seconds.toFixed(1)} s — found ${r.resources}${what}`;
}
