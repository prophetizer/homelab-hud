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
}

// Per-type widget payloads (data field), as the engine emits them.
export interface StaticData {
  text: string | null;
  links: { title: string; url: string }[];
}
export interface ResourceData {
  resource: Resource | null;
  fields: FieldValue[];
}
export interface ListItem {
  uid: string;
  name: string;
  state: State;
  stale: boolean;
  links: Record<string, string>;
  fields: FieldValue[];
}
export interface ListData {
  items: ListItem[];
  total: number;
  empty_text: string;
}
export interface MetricData {
  value: number | null;
  unit: Unit | null;
  ts: string | null;
  resource_name?: string;
  format?: { unit: string | null; precision: number };
  sparkline: { range: string; points: [number, number][] } | null;
}
export interface EmbedData {
  url: string;
  sandbox: "strict" | "relaxed";
  open_in: "workspace" | "inline";
  fallback: "card" | "new_tab";
  framing: Framing;
}
