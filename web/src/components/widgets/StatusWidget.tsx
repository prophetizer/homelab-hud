// SPDX-License-Identifier: Apache-2.0
import type { ResolvedWidget, State, StatusData } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";

const ORDER: State[] = ["down", "degraded", "unknown", "paused", "up"];

/** A status page's headline: all fine, or exactly what is not. */
export function StatusWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as StatusData;
  return (
    <WidgetFrame widget={widget} className="widget--status">
      <div className="status-banner" data-state={widget.state ?? undefined}>
        <span className="status-banner__dot status-dot" data-state={widget.state ?? undefined} />
        <span className="status-banner__headline">{data.headline}</span>
        <span className="status-banner__counts">
          {ORDER.filter((s) => data.counts?.[s]).map((s) => (
            <span key={s} className="status-banner__count" data-state={s}>
              {data.counts[s]} {s}
            </span>
          ))}
        </span>
      </div>
      {data.problems?.length ? (
        <ul className="status-banner__problems">
          {data.problems.map((p) => (
            <li key={p.uid}>
              <span className="status-dot" data-state={p.state} aria-label={p.state} />
              {p.links["ui"] ? (
                <a href={p.links["ui"]} target="_blank" rel="noreferrer noopener">
                  {p.title}
                </a>
              ) : (
                p.title
              )}
              <span className="list__state" data-state={p.state}>
                {p.state}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </WidgetFrame>
  );
}
