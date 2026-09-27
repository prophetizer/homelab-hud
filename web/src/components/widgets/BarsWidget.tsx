// SPDX-License-Identifier: Apache-2.0
import type { CSSProperties } from "react";
import { formatValue } from "../../api/format";
import type { BarsData, ResolvedWidget } from "../../api/types";
import { useTweened } from "../../hooks/useTweened";
import { WidgetFrame } from "../WidgetFrame";
import { Empty } from "./Empty";
import { valueParts } from "./MetricWidget";

/** One metric across many resources: a strip of columns (per-core CPU) or ranked rows
 *  (busiest containers). Each bar carries its own status colour. */
export function BarsWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as BarsData;
  const precision = data.format?.precision ?? 1;
  const fmt = (v: number | null) => formatValue(v, data.unit, precision);
  const pct = (v: number | null) => (v === null ? 0 : Math.max(0, Math.min(100, (v / data.max) * 100)));
  const summary = data.summary;
  const headline = useTweened(summary ? summary.value : null);
  const [number, unit] = summary ? valueParts(fmt(headline), data.unit) : ["", ""];
  return (
    <WidgetFrame widget={widget}>
      {summary ? (
        <p className="metric__value bars__summary" data-state={summary.state}>
          <span className="metric__number">{number}</span>
          {unit ? <span className="metric__unit">{unit}</span> : null}
          <span className="bars__caption">
            {summary.kind === "mean" ? "average" : "peak"} of {data.bars.length}
          </span>
        </p>
      ) : null}
      {data.bars.length === 0 ? (
        <Empty icon={widget.icon} text={data.empty_text} />
      ) : data.layout === "columns" ? (
        <div className="bars bars--columns" style={{ "--bars": data.bars.length } as CSSProperties}>
          {data.bars.map((b) => (
            <span
              key={b.uid}
              className="bars__col"
              data-state={b.state}
              data-stale={b.stale || undefined}
              title={`${b.title}: ${fmt(b.value)}`}
            >
              <span className="bars__fill" style={{ height: `${pct(b.value)}%` }} />
            </span>
          ))}
        </div>
      ) : (
        <ul className="bars bars--rows">
          {data.bars.map((b) => (
            <li key={b.uid} className="bars__row" data-stale={b.stale || undefined}>
              {b.links["ui"] ? (
                <a className="bars__label" href={b.links["ui"]} target="_blank" rel="noreferrer noopener">
                  {b.title}
                </a>
              ) : (
                <span className="bars__label">{b.title}</span>
              )}
              <span className="bars__track" data-state={b.state}>
                <span className="bars__fill" style={{ width: `${pct(b.value)}%` }} />
              </span>
              <span className="bars__value">{fmt(b.value)}</span>
            </li>
          ))}
        </ul>
      )}
      {data.total > data.bars.length ? (
        <p className="widget__meta">
          {data.bars.length} of {data.total}
        </p>
      ) : null}
    </WidgetFrame>
  );
}
