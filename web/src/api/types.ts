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
}
export interface ListData {
  items: ListItem[];
  total: number;
  empty_text: string;
  layout?: "rows" | "grid" | "cards";
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
