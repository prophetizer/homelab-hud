// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { fillIn, forecastText } from "./capacity";

describe("capacity wording", () => {
  it("says when a disk fills in rounded human terms", () => {
    expect(fillIn(0.4)).toBe("within a day");
    expect(fillIn(1)).toBe("in 1 day");
    expect(fillIn(9.6)).toBe("in 10 days");
    expect(fillIn(35)).toBe("in about 5 weeks");
    expect(fillIn(200)).toBe("in about 7 months");
    expect(fillIn(900)).toBe("in over a year");
  });

  it("dates only a filling disk, and marks a rough estimate", () => {
    const f = {
      verdict: "filling",
      days: 2.2,
      full_on: "2026-10-01",
      bytes_per_day: 192e9,
      confidence: "rough",
    } as const;
    expect(forecastText(f, "7d")).toMatch(/^\+178\.8 GiB\/day · full in 2 days \(.+\) · rough estimate$/);
    expect(forecastText({ verdict: "steady" }, "7d")).toBe("steady over 7d");
    expect(forecastText({ verdict: "too_little", span_days: 0.5 }, "7d")).toBe("not enough history yet (0.5 days)");
    expect(forecastText({ verdict: "unclear" }, "30d")).toBe("no clear trend over 30d");
  });
});
