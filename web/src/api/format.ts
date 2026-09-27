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

/** A number with thousands separators; `precision` decimals when it is not whole. */
export function formatNumber(value: number, precision = 1): string {
  const digits = Number.isInteger(value) ? 0 : precision;
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function formatDuration(seconds: number): string {
  // Sub-second values are real measurements (a DNS lookup takes ~18 ms): flooring them to
  // "0s" said the opposite of the truth. Show milliseconds below one second.
  if (Math.abs(seconds) < 1 && seconds !== 0) {
    const ms = Math.abs(seconds) * 1000;
    return `${ms < 10 ? ms.toFixed(1) : Math.round(ms)} ms`;
  }
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
    default:
      return formatNumber(value, precision);
  }
}

/** "70.6", "/ 128 GiB": a part and its whole on the whole's scale, so they read as one
 *  quantity ("70.6 / 128 GiB", not "70.6 GiB / 128 GiB" or "72,294 MiB / 128 GiB"). */
export function formatShare(value: number, total: number, unit: Unit | null, precision = 1): [string, string] {
  if (unit === "bytes" || unit === "bps") {
    const [units, base] = unit === "bytes" ? [BINARY, 1024] : [BITS, 1000];
    let i = 0;
    for (let t = Math.abs(total); t >= base && i < units.length - 1; t /= base) i += 1;
    const div = base ** i;
    const digits = i === 0 ? 0 : precision;
    const whole = Number((total / div).toFixed(digits)).toLocaleString("en-US");
    return [(value / div).toFixed(digits), `/ ${whole} ${units[i]}`];
  }
  return [formatValue(value, unit, precision), `/ ${formatValue(total, unit, precision)}`];
}

export function formatAge(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, Math.round((now - t) / 1000));
  return s < 5 ? "just now" : `${formatDuration(s)} ago`;
}
