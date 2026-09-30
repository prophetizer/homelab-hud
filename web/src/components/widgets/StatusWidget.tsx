// SPDX-License-Identifier: Apache-2.0
import type { ResolvedWidget, State, StatusData } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";

const ORDER: State[] = ["down", "degraded", "unknown", "paused", "up"];

/** A status page's headline: all fine, or exactly what is not. */
export function StatusWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as StatusData;
  if (data.style === "wall") return <StatusWall widget={widget} data={data} />;
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

/** style: wall — a square per resource, worst first, then the problems named: every check
 *  at a glance, with its name on hover. */
function StatusWall({ widget, data }: { widget: ResolvedWidget; data: StatusData }) {
  const up = data.counts?.up ?? 0;
  return (
    <WidgetFrame widget={widget} className="widget--status">
      <p className="status-wall__line">
        <b>{data.headline}</b>
        <span>
          {up} of {data.total} up
        </span>
      </p>
      <ul className="status-wall" aria-label="Every check">
        {(data.cells ?? []).map((c) => (
          <li key={c.uid} className="status-wall__cell" data-state={c.state} title={`${c.title}: ${c.state}`} />
        ))}
      </ul>
      {data.problems?.length ? (
        <ul className="status-wall__problems">
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
