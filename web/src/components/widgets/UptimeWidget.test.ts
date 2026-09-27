// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { cellState, formatSla, type UptimeCell } from "./UptimeWidget";

const cell = (p: Partial<UptimeCell>): UptimeCell => ({
  t: 0, up: 0, degraded: 0, paused: 0, down: 0, unknown: 0, not_observed: 0, ...p,
});

describe("uptime cells", () => {
  it("take the worst state in the bucket", () => {
    expect(cellState(cell({ up: 1700, down: 100 }))).toBe("down");
    expect(cellState(cell({ up: 1700, degraded: 100 }))).toBe("degraded");
    expect(cellState(cell({ up: 1800 }))).toBe("up");
  });
  it("leave time HUD did not see blank, never red", () => {
    expect(cellState(cell({ not_observed: 1800 }))).toBe("none");
    expect(cellState(cell({ unknown: 1800 }))).toBe("unknown");
  });
  it("show more digits the closer to 100 %", () => {
    expect(formatSla(99.987)).toBe("99.99 %");
    expect(formatSla(99.42)).toBe("99.4 %");
    expect(formatSla(87.6)).toBe("88 %");
  });
});

describe("calendar days", () => {
  it("colour a day by its uptime, not its worst minute", async () => {
    const { dayState } = await import("./UptimeWidget");
    expect(dayState(cell({ pct: 100 }))).toBe("up");
    expect(dayState(cell({ pct: 99.5 }))).toBe("degraded");
    expect(dayState(cell({ pct: 97 }))).toBe("down");
    expect(dayState(cell({ pct: null }))).toBe("none");
  });
});
