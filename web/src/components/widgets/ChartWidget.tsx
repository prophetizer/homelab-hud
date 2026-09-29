// SPDX-License-Identifier: Apache-2.0
import { useEffect, useMemo, useRef, useState } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { seriesStyle, stack, thresholdValue, withAlpha } from "../../api/chart";
import { fetchWidget } from "../../api/client";
import { formatValue } from "../../api/format";
import type { ChartData, ResolvedWidget, Unit } from "../../api/types";
import { usePoll } from "../../hooks/usePoll";
import { WidgetFrame } from "../WidgetFrame";
import { Empty } from "./Empty";

const RANGE_POLL_MS = 60_000;

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Bumps when the theme changes, so the canvas redraws in the new colours. */
function useThemeKey(): string {
  const [key, setKey] = useState(() => document.documentElement.dataset["theme"] ?? "");
  useEffect(() => {
    const obs = new MutationObserver(() => setKey(document.documentElement.dataset["theme"] ?? ""));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const onMedia = () => setKey((k) => `${k.split("|")[0]}|${media.matches ? "l" : "d"}`);
    media.addEventListener("change", onMedia);
    return () => {
      obs.disconnect();
      media.removeEventListener("change", onMedia);
    };
  }, []);
  return key;
}

function when(ts: number, timeZone: string): string {
  const opts: Intl.DateTimeFormatOptions = { weekday: "short", hour: "2-digit", minute: "2-digit" };
  try {
    return new Date(ts * 1000).toLocaleString(undefined, { ...opts, timeZone });
  } catch {
    return new Date(ts * 1000).toLocaleString(undefined, opts);
  }
}

function fmt(v: number | null | undefined, unit: Unit | null, precision: number): string {
  return v === null || v === undefined ? "—" : formatValue(v, unit, precision);
}

/** A metric over time. Series are neutral — tone, weight and dash, never hue — and only a
 *  threshold line carries a status colour (invariant 9). Gaps stay gaps. */
export function ChartWidget({ widget, board }: { widget: ResolvedWidget; board?: string | undefined }) {
  const initial = widget.data as unknown as ChartData;
  const [range, setRange] = useState<string | null>(null);
  // Another range is fetched on its own and refreshed each minute; the board's own poll
  // keeps serving the configured one.
  const other = usePoll(
    `chart:${board ?? ""}:${widget.id}:${range ?? ""}`,
    (signal) => (range && board ? fetchWidget(board, widget.id, range, signal) : Promise.resolve(null)),
    RANGE_POLL_MS,
  );
  const shown = range && other.data ? other.data : widget;
  const chart = shown.data as unknown as ChartData;
  const hasData = chart.series.some((s) => s.values.some((v) => v !== null));
  return (
    <WidgetFrame widget={shown}>
      <div className="chart">
        {board ? (
          <div className="chart__ranges" role="group" aria-label="Range">
            {initial.ranges.map((r) => {
              const active = (range ?? initial.range) === r;
              return (
                <button
                  key={r}
                  type="button"
                  className="chart__range"
                  aria-pressed={active}
                  onClick={() => setRange(r === initial.range ? null : r)}
                >
                  {r}
                </button>
              );
            })}
            {range && !other.data ? <span className="chart__loading">loading…</span> : null}
          </div>
        ) : null}
        {hasData ? (
          <Plot chart={chart} />
        ) : (
          <Empty icon={widget.icon} text={shown.error ?? "No history in this range yet"} />
        )}
      </div>
    </WidgetFrame>
  );
}

function Plot({ chart }: { chart: ChartData }) {
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const theme = useThemeKey();
  const unit = chart.unit;
  const precision = chart.format.precision;
  const raw = useMemo(() => chart.series.map((s) => s.values), [chart]);
  const drawn = useMemo(() => (chart.stacked ? stack(raw) : raw), [raw, chart.stacked]);
  // Rebuild only when the chart's shape changes; new data is a setData.
  const shape = [
    chart.series.map((s) => s.label).join("|"),
    chart.kind,
    chart.stacked,
    chart.timezone,
    unit,
    theme,
  ].join("#");

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const fg = cssVar("--fg-faint");
    // A canvas cannot resolve var(); give it the family the page actually uses.
    const axisFont = `11px ${cssVar("--font-sans") || "sans-serif"}`;
    const grid = cssVar("--border-soft");
    const tz = chart.timezone;
    const series: uPlot.Series[] = [
      {},
      ...chart.series.map((s, i) => {
        const style = seriesStyle(i);
        const colour = cssVar(style.tone);
        const line: uPlot.Series = {
          label: s.label,
          stroke: colour,
          width: style.width,
          dash: style.dash,
          points: { show: false },
          spanGaps: false,
        };
        if (chart.kind === "area" || chart.stacked) line.fill = withAlpha(colour, chart.stacked ? 0.14 : 0.08);
        return line;
      }),
    ];
    // Stacked areas fill between neighbours, not down to zero.
    const bands: uPlot.Band[] = chart.stacked
      ? chart.series.slice(1).map((_, i) => ({ series: [i + 2, i + 1] as [number, number] }))
      : [];
    const thresholdLines = chart.thresholds
      .map((t) => ({
        value: thresholdValue(t),
        colour: cssVar(t.state === "error" ? "--status-down" : "--status-degraded"),
      }))
      .filter((t): t is { value: number; colour: string } => t.value !== null);
    const opts: uPlot.Options = {
      width: Math.max(el.clientWidth, 120),
      height: Math.max(el.clientHeight, 80),
      series,
      bands,
      legend: { show: false },
      cursor: { points: { show: false }, drag: { x: false, y: false } },
      tzDate: (ts) => {
        try {
          return uPlot.tzDate(new Date(ts * 1000), tz);
        } catch {
          return new Date(ts * 1000);
        }
      },
      scales: { y: { range: (_u, min, max) => [Math.min(0, min), max > 0 ? max * 1.08 : 1] } },
      axes: [
        { stroke: fg, grid: { stroke: grid, width: 1 }, ticks: { show: false }, font: axisFont },
        {
          stroke: fg,
          grid: { stroke: grid, width: 1 },
          ticks: { show: false },
          size: 64,
          font: axisFont,
          values: (_u, splits) => splits.map((v) => formatValue(v, unit, 0)),
        },
      ],
      hooks: {
        setCursor: [(u) => setCursor(u.cursor.idx ?? null)],
        draw: [
          (u) => {
            const { ctx } = u;
            ctx.save();
            ctx.setLineDash([4, 4]);
            ctx.lineWidth = 1 * devicePixelRatio;
            for (const t of thresholdLines) {
              const y = u.valToPos(t.value, "y", true);
              if (y < u.bbox.top || y > u.bbox.top + u.bbox.height) continue;
              ctx.strokeStyle = t.colour;
              ctx.beginPath();
              ctx.moveTo(u.bbox.left, y);
              ctx.lineTo(u.bbox.left + u.bbox.width, y);
              ctx.stroke();
            }
            ctx.restore();
          },
        ],
      },
    };
    const u = new uPlot(opts, [chart.x, ...drawn] as uPlot.AlignedData, el);
    plot.current = u;
    const resize = new ResizeObserver(() =>
      u.setSize({ width: Math.max(el.clientWidth, 120), height: Math.max(el.clientHeight, 80) }),
    );
    resize.observe(el);
    return () => {
      resize.disconnect();
      u.destroy();
      plot.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape]);

  useEffect(() => {
    plot.current?.setData([chart.x, ...drawn] as uPlot.AlignedData);
  }, [chart.x, drawn]);

  const at = cursor;
  return (
    <>
      <div ref={host} className="chart__plot" />
      <ul className="chart__legend">
        {chart.series.map((s, i) => {
          const style = seriesStyle(i);
          const value = at !== null ? (raw[i]?.[at] ?? null) : s.last;
          return (
            <li key={s.uid + s.metric} className="chart__key" data-stale={s.stale || undefined}>
              <svg className="chart__swatch" width="18" height="6" aria-hidden="true">
                <line
                  x1="0"
                  y1="3"
                  x2="18"
                  y2="3"
                  stroke={`var(${style.tone})`}
                  strokeWidth={style.width + 0.5}
                  strokeDasharray={style.dash.join(" ")}
                />
              </svg>
              <span className="chart__label">{s.label}</span>
              <span className="chart__value">{fmt(value, s.unit ?? unit, precision)}</span>
              {at === null ? (
                <span className="chart__range-stats">
                  max {fmt(s.max, s.unit ?? unit, precision)} · avg {fmt(s.mean, s.unit ?? unit, precision)}
                </span>
              ) : null}
            </li>
          );
        })}
        {at !== null && chart.x[at] !== undefined ? (
          <li className="chart__at">{when(chart.x[at] as number, chart.timezone)}</li>
        ) : null}
      </ul>
    </>
  );
}
