// SPDX-License-Identifier: Apache-2.0
// YAML grid is 1-based {col,row,w,h}; react-grid-layout is 0-based {x,y,w,h}.
import type { Placement, ResolvedBoard, ResolvedSection, ResolvedWidget } from "./types";

export interface Cell {
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export function toCells(board: ResolvedBoard): Cell[] {
  return board.widgets.map((w) => ({ i: w.id, x: w.grid.col - 1, y: w.grid.row - 1, w: w.grid.w, h: w.grid.h }));
}

export function toPlacements(cells: readonly Cell[]): Placement[] {
  return cells.map((it) => ({ id: it.i, grid: { col: it.x + 1, row: it.y + 1, w: it.w, h: it.h } }));
}

/** Only the widgets whose cell differs from `before`: the PATCH is minimal by design. */
export function changedPlacements(before: readonly Cell[], after: readonly Cell[]): Placement[] {
  const prev = new Map(before.map((it) => [it.i, it]));
  return toPlacements(
    after.filter((it) => {
      const p = prev.get(it.i);
      return !p || p.x !== it.x || p.y !== it.y || p.w !== it.w || p.h !== it.h;
    }),
  );
}

/** Tiles grouped for display: the ones in no section first (untitled), then each section in
 *  the board's order. A board without sections is one untitled group — as before. */
export function sectionGroups(board: ResolvedBoard): { section: ResolvedSection | null; widgets: ResolvedWidget[] }[] {
  const sections = board.sections ?? [];
  const known = new Set(sections.map((s) => s.id));
  const loose = board.widgets.filter((w) => !w.section || !known.has(w.section));
  const groups = sections.map((s) => ({ section: s, widgets: board.widgets.filter((w) => w.section === s.id) }));
  return [...(loose.length > 0 || sections.length === 0 ? [{ section: null, widgets: loose }] : []), ...groups];
}
