// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { exportUrl, runsByDate } from "./reports";

describe("runsByDate", () => {
  it("groups one run's formats together, newest first", () => {
    const f = (name: string) => ({ name, format: name.split(".").pop() ?? "", size: 1, modified: "" });
    const runs = runsByDate(
      [f("weekly-2026-09-21.html"), f("weekly-2026-09-28.csv"), f("weekly-2026-09-28.html")],
      "weekly",
    );
    expect(runs.map((r) => [r.date, r.files.length])).toEqual([
      ["2026-09-28", 2],
      ["2026-09-21", 1],
    ]);
  });

  it("orders one run's files View, PDF, CSV", () => {
    const f = (name: string) => ({ name, format: name.split(".").pop() ?? "", size: 1, modified: "" });
    const runs = runsByDate(
      [f("weekly-2026-09-28.csv"), f("weekly-2026-09-28.pdf"), f("weekly-2026-09-28.html")],
      "weekly",
    );
    expect(runs.map((r) => r.files.map((x) => x.format))).toEqual([["html", "pdf", "csv"]]);
  });
});

describe("exportUrl", () => {
  it("asks for one board over one range in one format, escaped", () => {
    expect(exportUrl("lab", "30d", "csv")).toBe("/api/v1/reports/adhoc?board=lab&range=30d&format=csv");
    expect(exportUrl("a&b", "24h", "pdf")).toBe("/api/v1/reports/adhoc?board=a%26b&range=24h&format=pdf");
  });
});
