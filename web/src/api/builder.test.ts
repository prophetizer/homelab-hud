// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { build, changes, defaults, fromWidget, typesFor, unmanaged, type Group } from "./builder";

const activity: Group = {
  provider: "tautulli",
  kind: "activity",
  count: 1,
  resources: [{ uid: "tautulli:activity:main", name: "Activity", state: "up" }],
  metrics: { bandwidth_bps: "bps", streams: "count" },
  attrs: [],
};
const disks: Group = {
  provider: "glances",
  kind: "filesystem",
  count: 3,
  resources: [],
  metrics: { free_bytes: "bytes", used_pct: "pct" },
  attrs: [],
};

describe("widget builder", () => {
  it("offers only the types that can show the selection", () => {
    expect(typesFor({ provider: "tautulli", kind: "activity", uid: "tautulli:activity:main" }, activity)).toEqual([
      "metric",
      "chart",
      "resource",
      "uptime",
    ]);
    expect(typesFor({ provider: "glances", kind: "filesystem" }, disks)).toEqual(["list", "status", "uptime", "bars", "capacity"]);
    const noMetrics = { ...activity, metrics: {} };
    expect(typesFor({ provider: "x", kind: "y" }, noMetrics)).toEqual(["list", "status", "uptime"]);
  });

  it("writes the widget a person would write, and reads it back", () => {
    const sel = { provider: "tautulli", kind: "activity", uid: "tautulli:activity:main" };
    const o = { ...defaults("metric", sel, activity), metric: "bandwidth_bps", style: "gauge", section: "now" };
    const w = build("metric", sel, o);
    expect(w).toEqual({
      type: "metric",
      title: "Activity bandwidth bps",
      section: "now",
      grid: { w: 1, h: 1 },
      source: { resource: "tautulli:activity:main", metric: "bandwidth_bps" },
      display: { style: "gauge", sparkline: { range: "6h" } },
    });
    const back = fromWidget({ id: "x", ...w });
    expect(back?.type).toBe("metric");
    expect(back?.sel).toEqual(sel);
    expect(build("metric", back!.sel, back!.options)).toEqual(w);
  });

  it("leaves a shape it does not write to YAML", () => {
    expect(fromWidget({ type: "list", source: { select: { provider: ["a", "b"], kind: "x" } } })).toBeNull();
    expect(fromWidget({ type: "embed", source: { url: "http://x" } })).toBeNull();
    expect(
      fromWidget({ type: "chart", source: { series: [{ resource: "a:b:c", metric: "m" }, { resource: "a:b:d", metric: "m" }] } }),
    ).toBeNull();
  });

  it("edits only what changed: a dropped trend is removed, the author's keys stay", () => {
    const before = {
      id: "bw",
      type: "metric",
      title: "Bandwidth",
      source: { resource: "tautulli:activity:main", metric: "bandwidth_bps" },
      display: { style: "number", sparkline: { range: "6h" }, delta: "1h" },
    };
    const read = fromWidget(before)!;
    const built = build("metric", read.sel, { ...read.options, range: "", style: "gauge" });
    expect(changes("metric", before, built)).toEqual({ display: { style: "gauge", sparkline: null } });
    expect(unmanaged("metric", before)).toEqual(["display.delta"]);
  });

  it("builds a storage forecast from the percentage metric", () => {
    const sel = { provider: "glances", kind: "filesystem" };
    const w = build("capacity", sel, defaults("capacity", sel, disks));
    expect(w.source).toEqual({ select: { provider: "glances", kind: "filesystem" } });
    expect(w.display).toEqual({ used: "used_pct", window: "7d", style: "rows" });
  });
});
