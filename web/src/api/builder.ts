// SPDX-License-Identifier: Apache-2.0
// The widget builder (PLAN §8.5 Flow B): what can be shown, which widget types fit it, and
// the YAML a form produces. Pure functions here; the panel is components/WidgetBuilder.tsx.
import { getJson, sendJson } from "./client";
import type { ResolvedBoard, ResolvedWidget } from "./types";

export interface Group {
  provider: string;
  kind: string;
  count: number;
  resources: { uid: string; name: string; state: string }[];
  metrics: Record<string, string | null>; // name → unit
  attrs: string[];
}

/** What the widget shows: every resource of a provider's kind, or one of them. */
export interface Selection {
  provider: string;
  kind: string;
  uid?: string; // one resource; absent = the whole group
}

export type WidgetType = "metric" | "resource" | "chart" | "uptime" | "list" | "bars" | "status" | "capacity";

export interface Options {
  title: string;
  section: string | null;
  w: number;
  h: number;
  metric: string | null; // metric, chart, bars, capacity; a card's lead
  stats: string[]; // a card's other readings
  style: string; // per type: gauge/number, lead/readings/hero/fields, wall/headline, line/area, a list layout
  range: string; // sparkline, chart, uptime, capacity window
  limit: number; // list, bars
}

export const TYPES: { type: WidgetType; label: string; hint: string }[] = [
  { type: "metric", label: "Number or gauge", hint: "One reading, large, with its trend" },
  { type: "resource", label: "Card", hint: "A resource's readings together" },
  { type: "chart", label: "Chart", hint: "A reading over hours or days" },
  { type: "uptime", label: "Uptime history", hint: "Up and down over time" },
  { type: "list", label: "List", hint: "Every one of them, as rows, cards or posters" },
  { type: "bars", label: "Bars", hint: "Them ranked by a reading" },
  { type: "status", label: "Status", hint: "All fine, or what is not" },
  { type: "capacity", label: "Storage forecast", hint: "How full, and when it fills" },
];

const pct = (g: Group) => Object.entries(g.metrics).filter(([, u]) => u === "pct").map(([m]) => m);
const numeric = (g: Group) => Object.keys(g.metrics);

/** The widget types that can show a selection: one resource or a group, and the metrics
 *  the group carries (a chart needs a reading; a forecast a percentage). */
export function typesFor(sel: Selection, g: Group): WidgetType[] {
  const hasMetrics = numeric(g).length > 0;
  const one: WidgetType[] = ["resource", "uptime"];
  if (hasMetrics) one.unshift("metric", "chart");
  const many: WidgetType[] = ["list", "status", "uptime"];
  if (hasMetrics) many.push("bars");
  if (pct(g).length > 0) many.push("capacity");
  return sel.uid ? one : many;
}

export function defaults(type: WidgetType, sel: Selection, g: Group): Options {
  const metrics = numeric(g);
  const first = (type === "capacity" ? pct(g)[0] : metrics[0]) ?? null;
  const name = sel.uid ? (g.resources.find((r) => r.uid === sel.uid)?.name ?? g.kind) : `${g.provider} ${g.kind}`;
  const wide = ["chart", "list", "uptime", "capacity", "bars"].includes(type);
  return {
    title: type === "metric" && first ? `${name} ${first.replace(/_/g, " ")}` : name,
    section: null,
    w: wide ? 2 : 1,
    h: type === "chart" || type === "list" ? 2 : 1,
    metric: first,
    stats: type === "resource" ? metrics.slice(0, 4) : [],
    style: { metric: "number", resource: "lead", chart: "line", list: "rows", status: "wall" }[type as string] ?? "",
    range: { chart: "24h", uptime: "24h", capacity: "7d", metric: "6h", resource: "6h" }[type as string] ?? "",
    limit: 8,
  };
}

/** The widget mapping a form produces — exactly what a person would write in the board. */
export function build(type: WidgetType, sel: Selection, o: Options): Record<string, unknown> {
  const w: Record<string, unknown> = { type, title: o.title.trim() || undefined };
  if (o.section) w.section = o.section;
  w.grid = { w: o.w, h: o.h };
  const select = { provider: sel.provider, kind: sel.kind };
  const uid = sel.uid ?? "";
  switch (type) {
    case "metric":
      w.source = { resource: uid, metric: o.metric };
      w.display = { style: o.style, ...(o.range ? { sparkline: { range: o.range } } : {}) };
      break;
    case "resource": {
      const stats = [o.metric, ...o.stats.filter((s) => s !== o.metric)].filter(Boolean) as string[];
      w.source = { resource: uid };
      w.display = {
        style: o.style,
        fields: [],
        stats: stats.map((m, i) => ({ resource: uid, metric: m, ...(i === 0 && o.range ? { sparkline: o.range } : {}) })),
      };
      break;
    }
    case "chart":
      w.source = { series: [{ resource: uid, metric: o.metric }], range: o.range };
      w.display = { kind: o.style };
      break;
    case "uptime":
      w.source = sel.uid ? { resource: uid } : { select };
      w.display = { range: o.range };
      break;
    case "list":
      w.source = { select };
      w.display = { layout: o.style };
      break;
    case "bars":
      w.source = { select, sort: [`-metric.${o.metric}`], limit: o.limit, metric: o.metric };
      break;
    case "status":
      w.source = { select };
      w.display = { style: o.style };
      break;
    case "capacity":
      w.source = { select };
      w.display = { used: o.metric, window: o.range, style: "rows" };
      break;
  }
  return JSON.parse(JSON.stringify(w)) as Record<string, unknown>; // drop undefined
}

/** The source/display keys each type writes. On an edit, one of these the form no longer
 *  sets is removed; any other key is the author's and is never touched. */
const OWNED: Record<WidgetType, { source: string[]; display: string[] }> = {
  metric: { source: ["resource", "metric"], display: ["style", "sparkline"] },
  resource: { source: ["resource"], display: ["style", "fields", "stats"] },
  chart: { source: ["series", "range"], display: ["kind"] },
  uptime: { source: ["resource", "select"], display: ["range"] },
  list: { source: ["select"], display: ["layout"] },
  bars: { source: ["select", "sort", "limit", "metric"], display: [] },
  status: { source: ["select"], display: ["style"] },
  capacity: { source: ["select"], display: ["used", "window", "style"] },
};

/** Keys of an existing widget this type's form does not write, as dotted paths
 *  ("display.delta"): kept exactly as written, and named in the panel as YAML-only. */
export function unmanaged(type: WidgetType, widget: Record<string, unknown>): string[] {
  const top = ["id", "type", "title", "section", "grid", "source", "display"];
  const out = Object.keys(widget).filter((k) => !top.includes(k));
  for (const key of ["source", "display"] as const) {
    const value = widget[key];
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const k of Object.keys(value)) if (!OWNED[type][key].includes(k)) out.push(`${key}.${k}`);
    }
  }
  return out;
}

/** An edit as changes for the server's merge: changed top-level keys; within source and
 *  display only this type's own keys — set, or null when the form dropped one. */
export function changes(
  type: WidgetType,
  before: Record<string, unknown>,
  built: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of ["title", "section"] as const) {
    if (JSON.stringify(built[key]) !== JSON.stringify(before[key])) out[key] = built[key] ?? null;
  }
  for (const key of ["source", "display"] as const) {
    const was = (before[key] ?? {}) as Record<string, unknown>;
    const now = (built[key] ?? {}) as Record<string, unknown>;
    const diff: Record<string, unknown> = {};
    for (const k of OWNED[type][key]) {
      if (k in now) {
        if (JSON.stringify(now[k]) !== JSON.stringify(was[k])) diff[k] = now[k];
      } else if (k in was) {
        diff[k] = null;
      }
    }
    if (Object.keys(diff).length > 0) out[key] = diff;
  }
  return out;
}

type Raw = Record<string, unknown>;
const obj = (v: unknown): Raw => (v && typeof v === "object" && !Array.isArray(v) ? (v as Raw) : {});
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
function fromUid(uid: string): Selection | null {
  const [provider, kind] = uid.split(":");
  return provider && kind ? { provider, kind, uid } : null;
}
function fromSelect(v: unknown): Selection | null {
  const s = obj(v);
  const provider = str(s.provider);
  const kind = str(s.kind);
  return provider && kind && Object.keys(s).length === 2 ? { provider, kind } : null;
}

/** An existing widget read back into the form, or null when it uses a shape the builder
 *  does not write (several providers, several series…) — then it is edited in YAML. */
export function fromWidget(w: Raw): { type: WidgetType; sel: Selection; options: Options } | null {
  const type = str(w.type) as WidgetType | null;
  if (!type || !TYPES.some((t) => t.type === type)) return null;
  const source = obj(w.source);
  const display = obj(w.display);
  const grid = obj(w.grid);
  const base: Options = {
    title: str(w.title) ?? "",
    section: str(w.section),
    w: Number(grid.w ?? 1),
    h: Number(grid.h ?? 1),
    metric: null,
    stats: [],
    style: "",
    range: "",
    limit: 8,
  };
  let sel: Selection | null = null;
  switch (type) {
    case "metric":
      sel = fromUid(str(source.resource) ?? "");
      Object.assign(base, {
        metric: str(source.metric),
        style: str(display.style) ?? "number",
        range: str(obj(display.sparkline).range) ?? "",
      });
      break;
    case "resource": {
      const uid = str(source.resource) ?? "";
      sel = fromUid(uid);
      const stats = Array.isArray(display.stats) ? display.stats.map(obj) : [];
      if (stats.some((s) => s.resource !== uid)) return null;
      const metrics = stats.map((s) => str(s.metric)).filter((m): m is string => m !== null);
      Object.assign(base, {
        style: str(display.style) ?? "fields",
        metric: metrics[0] ?? null,
        stats: metrics.slice(1),
        range: str(stats[0]?.sparkline) ?? "",
      });
      break;
    }
    case "chart": {
      const series = Array.isArray(source.series) ? source.series.map(obj) : [];
      if (series.length !== 1) return null;
      sel = fromUid(str(series[0]?.resource) ?? "");
      Object.assign(base, { metric: str(series[0]?.metric), range: str(source.range) ?? "24h", style: str(display.kind) ?? "line" });
      break;
    }
    case "uptime":
      sel = source.resource ? fromUid(str(source.resource) ?? "") : fromSelect(source.select);
      base.range = str(display.range) ?? "24h";
      break;
    case "list":
      sel = fromSelect(source.select);
      base.style = str(display.layout) ?? "rows";
      break;
    case "bars":
      sel = fromSelect(source.select);
      Object.assign(base, { metric: str(source.metric), limit: Number(source.limit ?? 8) });
      break;
    case "status":
      sel = fromSelect(source.select);
      base.style = str(display.style) ?? "headline";
      break;
    case "capacity":
      sel = fromSelect(source.select);
      Object.assign(base, { metric: str(display.used) ?? "used_pct", range: str(display.window) ?? "7d" });
      break;
  }
  return sel ? { type, sel, options: base } : null;
}

export const fetchCatalog = (signal?: AbortSignal) => getJson<{ groups: Group[] }>("/api/v1/builder/catalog", signal);
export const previewWidget = (board: string, widget: Record<string, unknown>) =>
  sendJson<ResolvedWidget>("POST", `/api/v1/boards/${encodeURIComponent(board)}/builder/preview`, { widget });
export const addWidget = (board: string, revision: string, widget: Record<string, unknown>) =>
  sendJson<ResolvedBoard>("POST", `/api/v1/boards/${encodeURIComponent(board)}/builder/widgets`, { revision, widget });
export const fetchWidgetYaml = (board: string, id: string) =>
  getJson<{ widget: Record<string, unknown>; revision: string }>(
    `/api/v1/boards/${encodeURIComponent(board)}/builder/widgets/${encodeURIComponent(id)}`,
  );
export const editWidget = (board: string, id: string, revision: string, change: Record<string, unknown>) =>
  sendJson<ResolvedBoard>("PATCH", `/api/v1/boards/${encodeURIComponent(board)}/builder/widgets/${encodeURIComponent(id)}`, {
    revision,
    changes: change,
  });
export const removeWidget = (board: string, id: string, revision: string) =>
  sendJson<ResolvedBoard>(
    "DELETE",
    `/api/v1/boards/${encodeURIComponent(board)}/builder/widgets/${encodeURIComponent(id)}?revision=${encodeURIComponent(revision)}`,
  );
