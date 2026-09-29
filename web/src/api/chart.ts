// SPDX-License-Identifier: Apache-2.0
/** Pure helpers for the chart widget: stacking, neutral series styles, threshold values. */

/** Each series added to the ones before it, bucket by bucket. A gap stays a gap only when
 *  every series has nothing there; otherwise a missing value adds nothing. */
export function stack(values: (number | null)[][]): (number | null)[][] {
  const out: (number | null)[][] = [];
  const n = values[0]?.length ?? 0;
  const empty = Array.from({ length: n }, (_, i) => values.every((s) => s[i] === null || s[i] === undefined));
  let running = new Array<number>(n).fill(0);
  for (const s of values) {
    running = running.map((sum, i) => sum + (s[i] ?? 0));
    out.push(running.map((sum, i) => (empty[i] ? null : sum)));
  }
  return out;
}

/** A CSS colour (#rgb, #rrggbb or rgb()) at an alpha, for a canvas fill. */
export function withAlpha(color: string, alpha: number): string {
  const c = color.trim();
  let r: number, g: number, b: number;
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c);
  const rgb = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i.exec(c);
  if (hex?.[1]) {
    const h = hex[1].length === 3 ? [...hex[1]].map((x) => x + x).join("") : hex[1];
    [r, g, b] = [0, 2, 4].map((i) => Number.parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
  } else if (rgb) {
    [r, g, b] = [rgb[1], rgb[2], rgb[3]].map(Number) as [number, number, number];
  } else {
    return c;
  }
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Series are told apart by tone, weight and dash — never by hue (invariant 9). */
export const SERIES_STYLES: { tone: "--fg" | "--fg-muted" | "--fg-faint"; width: number; dash: number[] }[] = [
  { tone: "--fg", width: 1.75, dash: [] },
  { tone: "--fg-muted", width: 1.5, dash: [6, 4] },
  { tone: "--fg-faint", width: 1.5, dash: [] },
  { tone: "--fg-muted", width: 1.25, dash: [2, 3] },
  { tone: "--fg", width: 1.25, dash: [8, 3, 2, 3] },
  { tone: "--fg-faint", width: 1.25, dash: [6, 4] },
];

export function seriesStyle(i: number): (typeof SERIES_STYLES)[number] {
  return SERIES_STYLES[i % SERIES_STYLES.length] as (typeof SERIES_STYLES)[number];
}

/** The value a threshold line sits at. */
export function thresholdValue(t: { gte: number | null; lte: number | null }): number | null {
  return t.gte ?? t.lte ?? null;
}
