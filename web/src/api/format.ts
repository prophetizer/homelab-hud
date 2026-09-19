// SPDX-License-Identifier: Apache-2.0
// Display formatting for canonical units. Storage is canonical (bytes, bps, pct 0-100…);
// this is the one place a value becomes human-readable.
import type { Unit } from "./types";

const BINARY = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
const BITS = ["bit/s", "kbit/s", "Mbit/s", "Gbit/s", "Tbit/s"];

function scaled(n: number, units: readonly string[], base: number, precision: number): string {
  let v = Math.abs(n);
  let i = 0;
  while (v >= base && i < units.length - 1) {
    v /= base;
    i += 1;
  }
  const sign = n < 0 ? "-" : "";
  const digits = i === 0 ? 0 : precision;
  return `${sign}${v.toFixed(digits)} ${units[i]}`;
}

export function formatDuration(seconds: number): string {
  const s = Math.floor(Math.abs(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

export function formatValue(value: unknown, unit: Unit | null, precision = 1): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value !== "number") {
    if (Array.isArray(value)) return value.map((v) => formatValue(v, null)).join(", ");
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }
  if (!Number.isFinite(value)) return "—";
  switch (unit) {
    case "bytes":
      return scaled(value, BINARY, 1024, precision);
    case "bps":
      return scaled(value, BITS, 1000, precision);
    case "pct":
      return `${value.toFixed(precision)} %`;
    case "seconds":
      return formatDuration(value);
    case "celsius":
      return `${value.toFixed(precision)} °C`;
    case "watts":
      return `${value.toFixed(precision)} W`;
    case "count":
      return Number.isInteger(value) ? String(value) : value.toFixed(precision);
    default:
      return Number.isInteger(value) ? String(value) : value.toFixed(precision);
  }
}

export function formatAge(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, Math.round((now - t) / 1000));
  return s < 5 ? "just now" : `${formatDuration(s)} ago`;
}
