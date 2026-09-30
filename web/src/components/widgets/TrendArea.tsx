// SPDX-License-Identifier: Apache-2.0

/** A reading's recent history as a soft neutral area: drawn behind a large number
 *  ("behind") or as a band beneath it ("flow"). No axes and no hover — it gives the number
 *  its context, the chart widget gives the detail. Neutral ink only (invariant 9). */
export function TrendArea({
  points,
  placement,
  title,
}: {
  points: [number, number][];
  placement: "behind" | "flow";
  title?: string;
}) {
  if (points.length < 2) return null;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const W = 100;
  const H = 40;
  // Headroom above the peak, so a flat line does not sit on the top edge.
  const y = (v: number) => H - 2 - ((v - y0) / (y1 - y0 || 1)) * (H - 10);
  const line = points.map(([px, py]) => `${(((px - x0) / (x1 - x0 || 1)) * W).toFixed(2)},${y(py).toFixed(2)}`).join(" L");
  return (
    <svg
      className={`trend-area trend-area--${placement}`}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={title ? `${title}, recent trend` : "recent trend"}
    >
      <path className="trend-area__fill" d={`M${line} L${W},${H} L0,${H} Z`} />
      <path className="trend-area__line" d={`M${line}`} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** "20–64 %": the range a trend covered, for the line beneath a reading. */
export function trendRange(points: [number, number][] | undefined, format: (v: number) => string): string | null {
  if (!points || points.length < 2) return null;
  const ys = points.map((p) => p[1]);
  const lo = format(Math.min(...ys));
  const hi = format(Math.max(...ys));
  return lo === hi ? `steady at ${lo}` : `${lo} – ${hi}`;
}
