// SPDX-License-Identifier: Apache-2.0
import type { CSSProperties } from "react";
import { formatDuration } from "../../api/format";
import type { ResolvedWidget, State, UptimeCell } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";
import { Empty } from "./Empty";

export type { UptimeCell };
interface UptimeRow {
  uid: string;
  title: string;
  state: State;
  links: Record<string, string>;
  cells: UptimeCell[];
  sla: number | null;
  down_seconds: number;
  unobserved_seconds: number;
  approximate: boolean;
}
interface UptimeData {
  range: string;
  style?: "strip" | "calendar";
  sla_range: string;
  show_sla: boolean;
  bucket_seconds: number;
  rows: UptimeRow[];
  total: number;
  empty_text: string;
}

/** A bucket's colour is its worst state; a bucket HUD never saw is left blank, not red. */
export function cellState(c: UptimeCell): State | "none" {
  if (c.down > 0) return "down";
  if (c.degraded > 0) return "degraded";
  if (c.up > 0) return "up";
  if (c.paused > 0) return "paused";
  if (c.unknown > 0) return "unknown";
  return "none";
}

/** A calendar day's colour by its uptime, not its worst minute: one five-minute blip
 *  should not paint a whole day red. No data stays blank. */
export function dayState(c: UptimeCell): State | "none" {
  if (c.pct === null || c.pct === undefined) return "none";
  if (c.pct >= 99.95) return "up";
  if (c.pct >= 99) return "degraded";
  return "down";
}

/** Weeks as columns, Monday at the top; the first column is padded to its weekday. */
function Calendar({ cells, title }: { cells: UptimeCell[]; title: string }) {
  const pad = cells[0]?.weekday ?? 0;
  return (
    <span className="calendar" role="img" aria-label={`${title}: daily uptime`}>
      {Array.from({ length: pad }, (_, i) => (
        <span key={`pad-${i}`} className="calendar__pad" />
      ))}
      {cells.map((c) => (
        <span
          key={c.t}
          className="uptime__cell calendar__day"
          data-state={dayState(c)}
          title={`${c.date ?? ""}: ${c.pct === null || c.pct === undefined ? "no data" : formatSla(c.pct)}${c.down > 0 ? ` · down ${formatDuration(c.down)}` : ""}`}
        />
      ))}
    </span>
  );
}

/** "99.95 %", with the precision a status page uses: more nines, more digits. */
export function formatSla(pct: number): string {
  if (pct >= 99.9) return `${pct.toFixed(2)} %`;
  if (pct >= 99) return `${pct.toFixed(1)} %`;
  return `${pct.toFixed(0)} %`;
}

function cellTitle(c: UptimeCell, width: number): string {
  const from = new Date(c.t * 1000);
  const to = new Date((c.t + width) * 1000);
  const fmt = (d: Date) => d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const parts = (["up", "degraded", "paused", "down", "unknown", "not_observed"] as const)
    .filter((k) => c[k] > 0)
    .map((k) => `${k === "not_observed" ? "not observed" : k} ${formatDuration(c[k])}`);
  return `${fmt(from)} – ${fmt(to)}\n${parts.join(" · ") || "no data"}`;
}

export function UptimeWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as UptimeData;
  if (!data.rows) return <WidgetFrame widget={widget}>{null}</WidgetFrame>;
  return (
    <WidgetFrame widget={widget}>
      <p className="uptime__caption">
        last {data.range}
        {data.show_sla ? ` · uptime over ${data.sla_range}` : ""}
      </p>
      {data.rows.length === 0 ? (
        <Empty icon={widget.icon} text={data.empty_text} />
      ) : (
        <ul className="uptime">
          {data.rows.map((row) => (
            <li key={row.uid} className={data.style === "calendar" ? "uptime__row uptime__row--calendar" : "uptime__row"}>
              <span className="uptime__title">
                <span className="status-dot" data-state={row.state} aria-label={row.state} />
                {row.links["ui"] ? (
                  <a href={row.links["ui"]} target="_blank" rel="noreferrer noopener">
                    {row.title}
                  </a>
                ) : (
                  row.title
                )}
              </span>
              {data.style === "calendar" ? (
                <Calendar cells={row.cells} title={row.title} />
              ) : (
              <span
                className="uptime__strip"
                style={{ "--cells": row.cells.length } as CSSProperties}
                role="img"
                aria-label={`${row.title}: ${row.sla === null ? "no data" : formatSla(row.sla)} over ${data.sla_range}`}
              >
                {row.cells.map((c) => (
                  <span key={c.t} className="uptime__cell" data-state={cellState(c)} title={cellTitle(c, data.bucket_seconds)} />
                ))}
              </span>
              )}
              {data.show_sla ? (
                <span
                  className="uptime__sla"
                  title={[
                    row.down_seconds > 0 ? `down ${formatDuration(row.down_seconds)}` : "no downtime",
                    row.unobserved_seconds > 0 ? `not observed ${formatDuration(row.unobserved_seconds)} (excluded)` : null,
                    row.approximate ? "includes history rebuilt from logged events" : null,
                  ]
                    .filter(Boolean)
                    .join("\n")}
                >
                  {row.sla === null ? "—" : `${row.approximate ? "≈" : ""}${formatSla(row.sla)}`}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {data.total > data.rows.length ? (
        <p className="widget__meta">
          {data.rows.length} of {data.total}
        </p>
      ) : null}
    </WidgetFrame>
  );
}
