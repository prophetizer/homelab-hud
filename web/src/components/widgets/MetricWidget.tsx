// SPDX-License-Identifier: Apache-2.0
import { formatAge, formatShare, formatValue } from "../../api/format";
import type { MetricData, ResolvedWidget } from "../../api/types";
import { useTweened } from "../../hooks/useTweened";
import { WidgetFrame } from "../WidgetFrame";
import { Gauge } from "./Gauge";
import { Meter } from "./Meter";

const W = 200;
const H = 40;

// Plain SVG polyline: enough for a tile-sized trend. uPlot arrives with the chart widget
// in Phase 2; pulling it in for a sparkline would be weight without benefit.
export function Sparkline({ points }: { points: [number, number][] }) {
  if (points.length < 2) return null;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y0 = Math.min(...ys);
  const y1 = Math.max(...ys);
  const sx = x1 === x0 ? 0 : W / (x1 - x0);
  const sy = y1 === y0 ? 0 : (H - 4) / (y1 - y0);
  const coords = points.map(([x, y]) => `${((x - x0) * sx).toFixed(1)},${(H - 2 - (y - y0) * sy).toFixed(1)}`);
  // A faint area under the line makes the trend readable at a glance; still neutral, since
  // colour is reserved for status (invariant 9).
  const area = `0,${H} ${coords.join(" ")} ${W},${H}`;
  return (
    <svg className="sparkline" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      <polygon points={area} className="sparkline__area" />
      <polyline points={coords.join(" ")} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

const SPLIT_UNITS = new Set(["pct", "bytes", "bps", "celsius", "watts"]);

/** "322.1 Mbit/s" → ["322.1", "Mbit/s"], so the unit can be set smaller than the number. */
export function valueParts(text: string, unit: string | null): [string, string] {
  if (unit && SPLIT_UNITS.has(unit)) {
    const m = /^(.*\d)\s+(\S+)$/.exec(text);
    if (m && m[1] !== undefined && m[2] !== undefined) return [m[1], m[2]];
  }
  return [text, ""];
}

export function MetricWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as MetricData;
  const precision = data.format?.precision ?? 1;
  const total = data.total && data.value !== null ? data.total : null;
  const value = useTweened(data.value);
  const pct = useTweened(total ? total.pct : data.unit === "pct" ? data.value : null);
  // With a total: "70.6 / 128 GiB" over a usage bar. Otherwise the value and its unit.
  const [number, unit] =
    total && value !== null
      ? formatShare(value, total.value, data.unit, precision)
      : valueParts(formatValue(value, data.unit, precision), data.unit);
  const age = data.ts ? `${data.resource_name ? `${data.resource_name} · ` : ""}updated ${formatAge(data.ts)}` : undefined;
  const range = data.sparkline && data.sparkline.points.length > 1 ? data.sparkline : null;
  const gauge = data.style === "gauge" && pct !== null;
  return (
    <WidgetFrame widget={widget}>
      {gauge ? (
        <div className="metric__gauge" title={age}>
          <Gauge pct={pct} state={widget.state}>
            <span className="metric__number">{pct.toFixed(pct < 10 ? 1 : 0)}</span>
            <span className="metric__unit">%</span>
          </Gauge>
          {total ? (
            <p className="metric__gauge-caption">
              <span className="metric__gauge-value">{number}</span> {unit}
            </p>
          ) : null}
        </div>
      ) : (
        <p className="metric__value" data-state={widget.state ?? undefined} title={age}>
          <span className="metric__number">{number}</span>
          {unit ? <span className="metric__unit">{unit}</span> : null}
        </p>
      )}
      {total && !gauge ? (
        <div className="metric__share">
          <Meter pct={total.pct} state={widget.state} label={`${total.pct.toFixed(0)} % used`} />
          <span className="metric__share-text">{total.pct.toFixed(total.pct < 10 ? 1 : 0)} % used</span>
        </div>
      ) : null}
      {range ? (
        <div className="metric__trend" data-state={widget.stale ? undefined : (widget.state ?? undefined)} title={`last ${range.range}`}>
          <Sparkline points={range.points} />
        </div>
      ) : null}
    </WidgetFrame>
  );
}
