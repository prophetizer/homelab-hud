// SPDX-License-Identifier: Apache-2.0
// Mirrors of the Pydantic response models. Kept in step by hand (no codegen yet).

export type State = "up" | "down" | "degraded" | "unknown" | "paused";
export type Unit = "pct" | "bytes" | "bps" | "count" | "seconds" | "celsius" | "watts" | "none";
export type Severity = "info" | "warn" | "error";

export interface Resource {
  uid: string;
  provider: string;
  kind: string;
  name: string;
  state: State;
  attrs: Record<string, unknown>;
  links: Record<string, string>;
  parent_uid: string | null;
  fetched_at: string;
  stale: boolean;
}

export interface FieldValue {
  key: string;
  label: string;
  value: unknown;
  unit: Unit | null;
}

export interface Grid {
  col: number;
  row: number;
  w: number;
  h: number;
}

export interface Layout {
  columns: { sm: number; md: number; lg: number };
  gap: number;
}

export interface Framing {
  allowed: boolean | null;
  reason: string;
  checked_at: number;
}

export interface ResolvedWidget {
  id: string;
  type: string;
  title: string | null;
  grid: Grid;
  state: State | null;
  stale: boolean;
  error: string | null;
  data: Record<string, unknown>;
  icon?: string | null; // header icon: widget.icon, or its single provider's
}

export interface ResolvedBoard {
  name: string;
  title: string;
  icon: string | null;
  layout: Layout;
  generation: number;
  revision: string; // of the board's YAML file; PATCH must present it
  resolved_at: string;
  widgets: ResolvedWidget[];
  summary?: Partial<Record<State, number>>; // distinct resources on the board, by state
}

export interface Placement {
  id: string;
  grid: Grid;
}

export interface BoardSummary {
  name: string;
  title: string;
  icon: string | null;
  widgets: number;
  unsupported: number;
  apps: number; // workspace apps; a board of only apps is an app category, not a dashboard
  state?: State | null; // the worst state among the resources it shows
  down?: number;
}

export interface ProviderHealth {
  name: string;
  tier: "declarative" | "plugin";
  status: "starting" | "ok" | "degraded" | "error" | "stopped";
  labels: Record<string, string>;
  groups: string[];
  last_poll: string | null;
  last_success: string | null;
  last_error: string | null;
  consecutive_failures: number;
  circuit_open_until: string | null;
  resource_count: number;
  timings?: { name: string; seconds: number; timeout: number; ok: boolean }[]; // slowest first
}

// Per-type widget payloads (data field), as the engine emits them.
export interface StaticData {
  text: string | null;
  links: { title: string; url: string }[];
}
export interface HeroStat {
  label: string;
  value: number | null;
  unit: Unit | null;
  state: State;
}
export interface ResourceData {
  resource: Resource | null;
  fields: FieldValue[];
  style?: "fields" | "hero";
  stats?: HeroStat[];
}
export interface ListItem {
  uid: string;
  name: string;
  title: string; // display.title's field, or the name

  state: State;
  stale: boolean;
  links: Record<string, string>;
  fields: FieldValue[];
  bar?: number | null; // display.bar: a percentage drawn as a usage bar
  icon?: string | null; // display.icon: a name /api/v1/icons serves
  image?: boolean; // display.image: /api/v1/images/<uid> has a poster for this row
  backdrop?: boolean; // ... and /api/v1/images/<uid>?variant=backdrop has background art
  uptime?: { cells: UptimeCell[]; sla: number | null } | null; // display.uptime: the last 24 h
  trend?: [number, number][]; // display.trend: the last 6 h of one metric
  group_count?: number; // display.group: how many rows this one stands for
  when?: { day: string; time: string | null } | null; // display.date: its day (and time) in settings.timezone
}
export interface ListData {
  items: ListItem[];
  total: number;
  empty_text: string;
  layout?: "rows" | "grid" | "cards" | "media" | "shelf" | "agenda" | "week";
  today?: string; // YYYY-MM-DD in settings.timezone, for agenda headings
  stats?: (HeroStat & { sparkline?: [number, number][] })[]; // display.stats: readings above the rows
  dense?: boolean; // display.dense: one line per row
}
export interface MetricData {
  value: number | null;
  unit: Unit | null;
  ts: string | null;
  resource_name?: string;
  format?: { unit: string | null; precision: number };
  sparkline: { range: string; points: [number, number][] } | null;
  total?: { value: number; unit: Unit; pct: number } | null; // display.total
  style?: "number" | "gauge";
  delta?: { window: string; then: number; change: number; pct: number | null } | null;
}
export interface Bar {
  uid: string;
  title: string;
  value: number | null;
  state: State;
  stale: boolean;
  links: Record<string, string>;
}
export interface BarsData {
  layout: "columns" | "rows";
  unit: Unit | null;
  max: number;
  format: { unit: string | null; precision: number };
  summary: { kind: "mean" | "max"; value: number; state: State } | null;
  bars: Bar[];
  total: number;
  empty_text: string;
}
export interface EmbedData {
  url: string;
  sandbox: "strict" | "relaxed";
  open_in: "workspace" | "inline";
  fallback: "card" | "new_tab";
  framing: Framing;
}

/** Mirrors hud/api/apps.py: an embed widget with `open_in: workspace`. */
export interface App {
  board: string;
  board_title: string;
  widget: string;
  title: string;
  url: string;
  sandbox: "strict" | "relaxed";
  fallback: "card" | "new_tab";
  state: State;
  error: string | null;
  framing: Framing;
  icon?: string | null;
}

/** One bucket of availability history: seconds per state, and what HUD did not see. */
export interface UptimeCell {
  t: number;
  date?: string; // calendar cells: the local day
  weekday?: number; // 0 = Monday
  pct?: number | null;
  up: number;
  degraded: number;
  paused: number;
  down: number;
  unknown: number;
  not_observed: number;
}

export interface StatusData {
  headline: string;
  counts: Partial<Record<State, number>>;
  total: number;
  problems: { uid: string; title: string; state: State; links: Record<string, string> }[];
}

export interface Incident {
  uid: string;
  title: string;
  state: State;
  start: number;
  end: number | null; // null: still going
  seconds: number;
  approximate: boolean;
  links: Record<string, string>;
}
export interface IncidentsData {
  range: string;
  incidents: Incident[];
  total: number;
  empty_text: string;
}

export interface ChartSeriesData {
  uid: string;
  metric: string;
  label: string;
  unit: Unit | null;
  last: number | null;
  min: number | null;
  max: number | null;
  mean: number | null;
  stale: boolean;
  values: (number | null)[];
}
export interface ChartData {
  range: string;
  ranges: string[];
  kind: "line" | "area";
  stacked: boolean;
  timezone: string;
  x: number[];
  series: ChartSeriesData[];
  unit: Unit | null;
  thresholds: { gte: number | null; lte: number | null; state: "warn" | "error" }[];
  format: { unit: string | null; precision: number };
}
export interface HeatmapData {
  grid: (number | null)[][];
  min: number | null;
  max: number | null;
  unit: Unit | null;
  range: string;
  agg: "mean" | "max";
  samples: number;
  resource_name: string;
  format: { unit: string | null; precision: number };
}
