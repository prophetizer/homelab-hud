// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { dayLabel, stateCounts, weekDays } from "./ListWidget";

describe("stateCounts", () => {
  it("leads with the worst state", () => {
    const items = [{ state: "up" }, { state: "down" }, { state: "up" }, { state: "unknown" }] as const;
    expect(stateCounts([...items])).toBe("1 down · 1 unknown · 2 up");
  });
  it("is empty for no items", () => {
    expect(stateCounts([])).toBe("");
  });
});

describe("dayLabel", () => {
  it("names the days around today and dates the rest", () => {
    expect(dayLabel("2026-09-27", "2026-09-27")).toBe("Today");
    expect(dayLabel("2026-09-28", "2026-09-27")).toBe("Tomorrow");
    expect(dayLabel("2026-09-26", "2026-09-27")).toBe("Yesterday");
    expect(dayLabel("2026-10-03", "2026-09-27")).toMatch(/3/);
    expect(dayLabel("not-a-day", "2026-09-27")).toBe("not-a-day");
  });
});

describe("weekDays", () => {
  it("is seven plain dates from today, across a month end", () => {
    expect(weekDays("2026-09-27")).toEqual([
      "2026-09-27",
      "2026-09-28",
      "2026-09-29",
      "2026-09-30",
      "2026-10-01",
      "2026-10-02",
      "2026-10-03",
    ]);
    expect(weekDays("nope")).toEqual([]);
  });
});
