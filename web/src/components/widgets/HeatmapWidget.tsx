// SPDX-License-Identifier: Apache-2.0
import { formatValue } from "../../api/format";
import type { HeatmapData, ResolvedWidget } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";
import { Empty } from "./Empty";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** 0..1 for a value on the grid's own scale; a flat grid reads as mid-strength. */
export function intensity(v: number, min: number, max: number): number {
  if (max <= min) return 0.5;
  return Math.max(0, Math.min(1, (v - min) / (max - min)));
}

/** A metric by weekday and hour, in settings.timezone. Strength is neutral — the text
 *  colour at more or less opacity — never a status colour (invariant 9). */
export function HeatmapWidget({ widget }: { widget: ResolvedWidget }) {
  const d = widget.data as unknown as HeatmapData;
  const { min, max } = d;
  if (min === null || max === null) {
    return (
      <WidgetFrame widget={widget}>
        <Empty icon={widget.icon} text={widget.error ?? "No history yet"} />
      </WidgetFrame>
    );
  }
  const show = (v: number) => formatValue(v, d.unit, d.format.precision);
  return (
    <WidgetFrame widget={widget}>
      <div className="heatmap" role="img" aria-label={`${d.resource_name} by weekday and hour, last ${d.range}`}>
        <div className="heatmap__grid">
          <span />
          {Array.from({ length: 24 }, (_, h) => (
            <span key={h} className="heatmap__hour">
              {h % 6 === 0 ? String(h).padStart(2, "0") : ""}
            </span>
          ))}
          {d.grid.map((row, day) => (
            <Row key={DAYS[day]} day={DAYS[day] ?? ""} row={row} min={min} max={max} show={show} agg={d.agg} />
          ))}
        </div>
        <p className="heatmap__scale">
          <span>{show(min)}</span>
          <span className="heatmap__ramp" aria-hidden="true" />
          <span>{show(max)}</span>
          <span className="heatmap__note">
            {d.agg === "max" ? "peak" : "average"} per hour · last {d.range}
          </span>
        </p>
      </div>
    </WidgetFrame>
  );
}

function Row({
  day,
  row,
  min,
  max,
  show,
  agg,
}: {
  day: string;
  row: (number | null)[];
  min: number;
  max: number;
  show: (v: number) => string;
  agg: string;
}) {
  return (
    <>
      <span className="heatmap__day">{day}</span>
      {row.map((v, h) => (
        <span
          key={h}
          className="heatmap__cell"
          data-empty={v === null || undefined}
          style={v === null ? undefined : { opacity: 0.08 + 0.87 * intensity(v, min, max) }}
          title={`${day} ${String(h).padStart(2, "0")}:00 · ${v === null ? "no data" : `${show(v)} (${agg})`}`}
        />
      ))}
    </>
  );
}
