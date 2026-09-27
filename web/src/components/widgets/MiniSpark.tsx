// SPDX-License-Identifier: Apache-2.0

/** A row-sized trend: a line only, no axes, no hover — the shape, at a glance. */
export function MiniSpark({ points, title }: { points: [number, number][]; title?: string }) {
  if (points.length < 2) return null;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const W = 60;
  const H = 16;
  const coords = points
    .map(([x, y]) => `${(((x - x0) / (x1 - x0 || 1)) * W).toFixed(1)},${(H - 1 - ((y - y0) / (y1 - y0 || 1)) * (H - 2)).toFixed(1)}`)
    .join(" ");
  return (
    <svg className="mini-spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={title ?? "trend"}>
      <polyline points={coords} fill="none" stroke="currentColor" strokeWidth="1.25" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
