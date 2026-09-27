// SPDX-License-Identifier: Apache-2.0
import { formatDuration } from "../../api/format";
import type { IncidentsData, ResolvedWidget } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";
import { Empty } from "./Empty";

function when(ts: number): string {
  const d = new Date(ts * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** "sonarr down 03:12 → 03:19 · 7 min": what happened, newest first. */
export function IncidentsWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as IncidentsData;
  if (!data.incidents) return <WidgetFrame widget={widget}>{null}</WidgetFrame>;
  return (
    <WidgetFrame widget={widget}>
      {data.incidents.length === 0 ? (
        <Empty icon={widget.icon} text={`${data.empty_text} in the last ${data.range}`} />
      ) : (
        <ol className="incidents">
          {data.incidents.map((i) => (
            <li key={`${i.uid}:${i.start}`} className="incident" data-state={i.state} data-open={i.end === null || undefined}>
              <span className="status-dot" data-state={i.state} aria-label={i.state} />
              <span className="incident__what">
                {i.links["ui"] ? (
                  <a href={i.links["ui"]} target="_blank" rel="noreferrer noopener">
                    {i.title}
                  </a>
                ) : (
                  i.title
                )}{" "}
                <span className="list__state" data-state={i.state}>
                  {i.state}
                </span>
              </span>
              <span className="incident__when">
                {i.end === null ? `since ${when(i.start)}` : `${when(i.start)} → ${when(i.end)}`}
              </span>
              <span className="incident__for" title={i.approximate ? "rebuilt from logged events" : undefined}>
                {i.approximate ? "≈" : ""}
                {/* Closed after under a minute: seen down, for how long HUD cannot say. */}
                {i.end !== null && i.seconds < 60 ? "< 1 min" : formatDuration(i.seconds)}
                {i.end === null ? " · ongoing" : ""}
              </span>
            </li>
          ))}
        </ol>
      )}
      {data.total > data.incidents.length ? (
        <p className="widget__meta">
          {data.incidents.length} of {data.total}
        </p>
      ) : null}
    </WidgetFrame>
  );
}
