// SPDX-License-Identifier: Apache-2.0
import { formatAge, formatValue } from "../../api/format";
import type { MetricData, ResolvedWidget } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";

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
  const d = points
    .map(([x, y]) => `${((x - x0) * sx).toFixed(1)},${(H - 2 - (y - y0) * sy).toFixed(1)}`)
    .join(" ");
  return (
    <svg className="sparkline" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      <polyline points={d} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function MetricWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as MetricData;
  const precision = data.format?.precision ?? 1;
  return (
    <WidgetFrame widget={widget}>
      <p className="metric__value" data-state={widget.state ?? undefined}>
        {formatValue(data.value, data.unit, precision)}
      </p>
      {data.sparkline && data.sparkline.points.length > 1 ? (
        <>
          <Sparkline points={data.sparkline.points} />
          <p className="widget__meta">last {data.sparkline.range}</p>
        </>
      ) : null}
      {data.ts ? (
        <p className="widget__meta">
          {data.resource_name ? `${data.resource_name} · ` : ""}
          {formatAge(data.ts)}
        </p>
      ) : null}
    </WidgetFrame>
  );
}
