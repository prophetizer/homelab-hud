// SPDX-License-Identifier: Apache-2.0
// What the sidebar shows, derived from the boards and apps the caller can see.
import type { App, BoardSummary } from "./types";

export interface AppGroup {
  board: string;
  title: string;
  apps: App[];
}

export interface Navigation {
  boards: BoardSummary[];
  appGroups: AppGroup[];
}

/**
 * Boards made entirely of workspace apps are app categories, not dashboards: listing them
 * among the boards put 70-odd flat entries in the sidebar and duplicated titles (an
 * "Infrastructure" board beside an "Infrastructure" app category). They move under Apps,
 * as collapsible groups in board order; boards with any other widget stay boards.
 */
export function buildNavigation(boards: readonly BoardSummary[], apps: readonly App[]): Navigation {
  const byBoard = new Map<string, App[]>();
  for (const a of apps) {
    const list = byBoard.get(a.board) ?? [];
    list.push(a);
    byBoard.set(a.board, list);
  }
  // From the board list itself, so the sidebar is right on first paint: /apps arrives later,
  // after probing every app's framing, and waiting for it briefly listed app categories as
  // boards again, duplicates included.
  const appOnly = (b: BoardSummary) => b.widgets > 0 && b.apps === b.widgets;
  const titles = new Map(boards.map((b) => [b.name, b.title]));
  const appGroups = [...byBoard.entries()]
    .map(([board, list]) => ({
      board,
      title: titles.get(board) ?? list[0]?.board_title ?? board,
      apps: [...list].sort((x, y) => x.title.localeCompare(y.title)),
    }))
    .sort((x, y) => x.title.localeCompare(y.title));
  return { boards: boards.filter((b) => !appOnly(b)), appGroups };
}

/**
 * Where "/" lands: a board named "home" if there is one, else the first dashboard by
 * title. Null when there are no dashboards yet — then the System page is the landing.
 * (It used to be the System page always: a grid of provider internals as the first thing
 * anyone saw.)
 */
export function landingBoard(boards: readonly BoardSummary[]): string | null {
  const home = boards.find((b) => b.name === "home");
  if (home) return home.name;
  const first = [...boards].sort((a, b) => a.title.localeCompare(b.title))[0];
  return first ? first.name : null;
}
