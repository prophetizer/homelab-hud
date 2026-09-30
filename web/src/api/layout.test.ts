// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { changedPlacements, sectionGroups, toCells, toPlacements } from "./layout";
import type { ResolvedBoard } from "./types";

const board = {
  widgets: [
    { id: "a", grid: { col: 1, row: 1, w: 1, h: 1 } },
    { id: "b", grid: { col: 3, row: 2, w: 2, h: 3 } },
  ],
} as unknown as ResolvedBoard;

describe("layout mapping", () => {
  it("converts 1-based YAML cells to 0-based grid cells and back", () => {
    const cells = toCells(board);
    expect(cells).toEqual([
      { i: "a", x: 0, y: 0, w: 1, h: 1 },
      { i: "b", x: 2, y: 1, w: 2, h: 3 },
    ]);
    expect(toPlacements(cells)).toEqual([
      { id: "a", grid: { col: 1, row: 1, w: 1, h: 1 } },
      { id: "b", grid: { col: 3, row: 2, w: 2, h: 3 } },
    ]);
  });

  it("reports only the cells that moved or resized", () => {
    const before = toCells(board);
    const after = before.map((c) => (c.i === "b" ? { ...c, x: 0, h: 1 } : c));
    expect(changedPlacements(before, after)).toEqual([{ id: "b", grid: { col: 1, row: 2, w: 2, h: 1 } }]);
    expect(changedPlacements(before, before)).toEqual([]);
  });
});

describe("sectionGroups", () => {
  const w = (id: string, section?: string) => ({ id, section: section ?? null }) as unknown as ResolvedBoard["widgets"][number];
  it("is one untitled group on a board without sections", () => {
    const groups = sectionGroups({ widgets: [w("a"), w("b")] } as unknown as ResolvedBoard);
    expect(groups.map((g) => [g.section, g.widgets.map((x) => x.id)])).toEqual([[null, ["a", "b"]]]);
  });
  it("puts loose tiles first, then each section in the board's order", () => {
    const sections = [
      { id: "media", title: "Media", stats: [] },
      { id: "system", title: "System", stats: [] },
    ];
    const groups = sectionGroups({ sections, widgets: [w("h", "system"), w("x"), w("p", "media")] } as unknown as ResolvedBoard);
    expect(groups.map((g) => [g.section?.id ?? null, g.widgets.map((x) => x.id)])).toEqual([
      [null, ["x"]],
      ["media", ["p"]],
      ["system", ["h"]],
    ]);
  });
});
