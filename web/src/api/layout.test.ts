// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { changedPlacements, toCells, toPlacements } from "./layout";
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
