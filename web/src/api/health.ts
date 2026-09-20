// SPDX-License-Identifier: Apache-2.0
// Mirrors hud/api/health.py. Kept in step by hand.
import type { ProviderHealth } from "./types";

export interface Health {
  status: "ok" | "degraded";
  app_version: string;
  // Everything below needs `providers:view`; anonymous and unprivileged callers get
  // liveness only (hud/api/health.py PublicHealth).
  started_at?: string;
  uptime_seconds?: number;
  config?: {
    version: string;
    loaded_at: string;
    warnings: number;
    error: string | null;
  };
  db?: {
    size_bytes: number;
    revisions: Record<string, string>;
  };
  providers?: ProviderHealth[];
}

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const res = await fetch("/api/v1/health", { signal: signal ?? null, cache: "no-store" });
  if (!res.ok) {
    throw new Error(`health ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as Health;
}

export function formatBytes(n: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

export function formatUptime(seconds: number): string {
  const s = Math.floor(seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${s % 60}s`;
}
