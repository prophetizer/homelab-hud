// SPDX-License-Identifier: Apache-2.0
import { formatValue } from "./format";
import type { CapacityForecast } from "./types";

/** "in 3 days", "in about 5 weeks", "in over a year". */
export function fillIn(days: number): string {
  if (days < 1) return "within a day";
  if (days < 14) return `in ${Math.round(days)} day${Math.round(days) === 1 ? "" : "s"}`;
  if (days < 60) return `in about ${Math.round(days / 7)} weeks`;
  if (days < 365) return `in about ${Math.round(days / 30)} months`;
  return "in over a year";
}

/** The forecast in words — only a filling disk with enough history gets a date. */
export function forecastText(f: CapacityForecast, window: string): string {
  switch (f.verdict) {
    case "filling": {
      const rate = f.bytes_per_day !== undefined ? `+${formatValue(f.bytes_per_day, "bytes", 1)}/day · ` : "";
      const when = f.days !== undefined ? `full ${fillIn(f.days)}` : "filling";
      const date = f.full_on && f.days !== undefined && f.days < 365 ? ` (${shortDate(f.full_on)})` : "";
      return `${rate}${when}${date}${f.confidence === "rough" ? " · rough estimate" : ""}`;
    }
    case "steady":
      return `steady over ${window}`;
    case "shrinking":
      return `shrinking over ${window}`;
    case "unclear":
      return `no clear trend over ${window}`;
    case "too_little":
      return `not enough history yet${f.span_days !== undefined ? ` (${f.span_days.toFixed(1)} days)` : ""}`;
    default:
      return "no reading";
  }
}

function shortDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  return Number.isNaN(d.getTime())
    ? iso
    : new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }).format(
        d,
      );
}
