// SPDX-License-Identifier: Apache-2.0
import { forecastText } from "../../api/capacity";
import { formatValue } from "../../api/format";
import type { CapacityData, ResolvedWidget } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";
import { Empty } from "./Empty";

const R = 19;
const C = 2 * Math.PI * R;

/** Disks and when they fill. The ring is how full each one is, in its state's colour — a
 *  disk due to fill within warn_days is amber, within error_days red. */
export function CapacityWidget({ widget }: { widget: ResolvedWidget }) {
  const d = widget.data as unknown as CapacityData;
  if (d.items.length === 0) {
    return (
      <WidgetFrame widget={widget}>
        <Empty icon={widget.icon} text={d.empty_text} />
      </WidgetFrame>
    );
  }
  if (d.style === "rows") {
    return (
      <WidgetFrame widget={widget}>
        <ul className="cap-rows">
          {d.items.map((i) => (
            <li key={i.uid} className="cap-rows__item" data-state={i.state} data-stale={i.stale || undefined}>
              <span className="cap-rows__name" title={i.title}>
                {i.title}
              </span>
              <span className="cap-rows__bar" aria-hidden="true">
                <i data-state={i.state} style={{ width: `${Math.max(0, Math.min(100, i.used_pct ?? 0))}%` }} />
              </span>
              <span className="cap-rows__pct">{i.used_pct === null ? "—" : `${Math.round(i.used_pct)} %`}</span>
              <span className="cap-rows__forecast" data-verdict={i.forecast.verdict}>
                {forecastText(i.forecast, d.window)}
              </span>
            </li>
          ))}
        </ul>
      </WidgetFrame>
    );
  }
  return (
    <WidgetFrame widget={widget}>
      <ul className="cap">
        {d.items.map((i) => {
          const used = i.used_pct;
          const on = used === null ? 0 : (Math.max(0, Math.min(100, used)) / 100) * C;
          const size =
            i.free_bytes !== null && i.total_bytes !== null
              ? `${formatValue(i.free_bytes, "bytes", 1)} free of ${formatValue(i.total_bytes, "bytes", 1)}`
              : null;
          return (
            <li key={i.uid} className="cap__item" data-state={i.state} data-stale={i.stale || undefined}>
              <span className="cap__ring">
                <svg viewBox="0 0 44 44" aria-hidden="true">
                  <circle className="cap__track" cx="22" cy="22" r={R} />
                  <circle
                    className="cap__arc"
                    data-state={i.state}
                    cx="22"
                    cy="22"
                    r={R}
                    strokeDasharray={`${on} ${C - on}`}
                    transform="rotate(-90 22 22)"
                  />
                </svg>
                <span className="cap__pct">{used === null ? "—" : `${Math.round(used)}%`}</span>
              </span>
              <span className="cap__name" title={i.title}>
                {i.title}
              </span>
              {size ? <span className="cap__size">{size}</span> : null}
              <span className="cap__forecast" data-verdict={i.forecast.verdict}>
                {forecastText(i.forecast, d.window)}
              </span>
            </li>
          );
        })}
      </ul>
    </WidgetFrame>
  );
}
