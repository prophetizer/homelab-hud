// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
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
export function Sparkline({ points, format }: { points: [number, number][]; format?: (v: number) => string }) {
  const [hover, setHover] = useState<number | null>(null);
  if (points.length < 2) return null;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y0 = Math.min(...ys);
  const y1 = Math.max(...ys);
  const sx = x1 === x0 ? 0 : W / (x1 - x0);
  const sy = y1 === y0 ? 0 : (H - 4) / (y1 - y0);
  const px = (x: number) => (x - x0) * sx;
  const py = (y: number) => H - 2 - (y - y0) * sy;
  const coords = points.map(([x, y]) => `${px(x).toFixed(1)},${py(y).toFixed(1)}`);
  // A faint area under the line makes the trend readable at a glance; its colour is the
  // tile's status (invariant 9).
  const area = `0,${H} ${coords.join(" ")} ${W},${H}`;
  const fmt = format ?? ((v: number) => v.toFixed(1));
  const mean = ys.reduce((a, b) => a + b, 0) / ys.length;
  const at = hover === null ? null : points[hover];
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const x = x0 + ((e.clientX - box.left) / box.width) * (x1 - x0);
    let best = 0;
    for (let i = 1; i < points.length; i += 1) {
      if (Math.abs((points[i]?.[0] ?? 0) - x) < Math.abs((points[best]?.[0] ?? 0) - x)) best = i;
    }
    setHover(best);
  };
  return (
    <div className="sparkline__wrap">
      <svg
        className="sparkline"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`trend: min ${fmt(y0)}, average ${fmt(mean)}, max ${fmt(y1)}`}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        <polygon points={area} className="sparkline__area" />
        <polyline points={coords.join(" ")} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        {at ? (
          <line className="sparkline__cursor" x1={px(at[0])} x2={px(at[0])} y1={0} y2={H} vectorEffect="non-scaling-stroke" />
        ) : null}
      </svg>
      {at ? (
        <span className="sparkline__tip" style={{ left: `${(px(at[0]) / W) * 100}%` }}>
          <strong>{fmt(at[1])}</strong> {new Date(at[0] < 1e12 ? at[0] * 1000 : at[0]).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </span>
      ) : null}
      <span className="sparkline__stats">
        <span>min {fmt(y0)}</span>
        <span>avg {fmt(mean)}</span>
        <span>max {fmt(y1)}</span>
      </span>
    </div>
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

/** "↑ 12 % vs 1h": which way the value moved. Neutral on purpose — up is not good or bad,
 *  and colour is reserved for status (invariant 9). */
export function deltaText(d: NonNullable<MetricData["delta"]>, unit: MetricData["unit"], precision: number): string {
  const arrow = d.change > 0 ? "↑" : d.change < 0 ? "↓" : "→";
  const size =
    d.pct !== null && unit !== "pct"
      ? `${Math.abs(d.pct) < 10 ? Math.abs(d.pct).toFixed(1) : Math.abs(d.pct).toFixed(0)} %`
      : formatValue(Math.abs(d.change), unit, precision);
  return d.change === 0 ? `→ no change vs ${d.window}` : `${arrow} ${size} vs ${d.window}`;
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
      {data.delta ? (
        <p className="metric__delta" data-dir={data.delta.change > 0 ? "up" : data.delta.change < 0 ? "down" : "flat"}>
          {deltaText(data.delta, data.unit, precision)}
        </p>
      ) : null}
      {total && !gauge ? (
        <div className="metric__share">
          <Meter pct={total.pct} state={widget.state} label={`${total.pct.toFixed(0)} % used`} />
          <span className="metric__share-text">{total.pct.toFixed(total.pct < 10 ? 1 : 0)} % used</span>
        </div>
      ) : null}
      {range ? (
        <div className="metric__trend" data-state={widget.stale ? undefined : (widget.state ?? undefined)} title={`last ${range.range}`}>
          <Sparkline points={range.points} format={(v) => formatValue(v, data.unit, precision)} />
        </div>
      ) : null}
    </WidgetFrame>
  );
}
