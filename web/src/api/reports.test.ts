// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { runsByDate } from "./reports";

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
});
