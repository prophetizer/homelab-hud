// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { buildNavigation, landingBoard } from "./nav";
import type { App, BoardSummary } from "./types";

const board = (name: string, title: string, widgets: number, apps = 0): BoardSummary => ({ name, title, icon: null, widgets, unsupported: 0, apps });
const app = (b: string, title: string): App =>
  ({ board: b, board_title: b, widget: title.toLowerCase(), title, url: "https://x", sandbox: "strict",
     fallback: "new_tab", state: "up", error: null, framing: { allowed: true, reason: "", checked_at: 0 } });

describe("buildNavigation", () => {
  it("moves app-only boards under Apps and ends duplicate titles", () => {
    const nav = buildNavigation(
      [board("infra", "Infrastructure", 9), board("apps-infrastructure", "Infrastructure", 2, 2), board("home", "Home", 14)],
      [app("apps-infrastructure", "Traefik"), app("apps-infrastructure", "Authelia")],
    );
    expect(nav.boards.map((b) => b.name)).toEqual(["infra", "home"]);
    expect(nav.appGroups).toEqual([
      { board: "apps-infrastructure", title: "Infrastructure", apps: [expect.objectContaining({ title: "Authelia" }), expect.objectContaining({ title: "Traefik" })] },
    ]);
  });

  it("keeps a board that mixes apps with other widgets", () => {
    const nav = buildNavigation([board("media", "Media", 8, 1)], [app("media", "Plex")]);
    expect(nav.boards.map((b) => b.name)).toEqual(["media"]);
    expect(nav.appGroups.map((g) => g.board)).toEqual(["media"]);
  });
});

it("classifies boards before /apps has answered", () => {
  const nav = buildNavigation([board("apps-media", "Media", 14, 14), board("media", "Media", 8)], []);
  expect(nav.boards.map((b) => b.name)).toEqual(["media"]);
  expect(nav.appGroups).toEqual([]);
});

it("lands on home, else the first dashboard, else nowhere", () => {
  expect(landingBoard([board("media", "Media", 8), board("home", "Home", 3)])).toBe("home");
  expect(landingBoard([board("media", "Media", 8), board("infra", "Infrastructure", 3)])).toBe("infra");
  expect(landingBoard([])).toBeNull();
});
