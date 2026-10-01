// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { appPath, boardPath, parseRoute } from "./router";

describe("parseRoute", () => {
  it("maps paths to routes", () => {
    expect(parseRoute("/")).toEqual({ kind: "home" });
    expect(parseRoute("")).toEqual({ kind: "home" });
    expect(parseRoute("/system")).toEqual({ kind: "system" });
    expect(parseRoute("/connect")).toEqual({ kind: "connect" });
    expect(parseRoute("/account")).toEqual({ kind: "account" });
    expect(parseRoute("/users")).toEqual({ kind: "users" });
    expect(parseRoute("/boards/media")).toEqual({ kind: "board", name: "media" });
    expect(parseRoute("/boards/media/")).toEqual({ kind: "board", name: "media" });
    expect(parseRoute("/boards/")).toEqual({ kind: "missing", path: "/boards" });
    expect(parseRoute("/boards/a b")).toEqual({ kind: "missing", path: "/boards/a b" });
    expect(parseRoute("/nope")).toEqual({ kind: "missing", path: "/nope" });
    expect(parseRoute("/apps/media/tautulli")).toEqual({ kind: "app", board: "media", widget: "tautulli" });
    expect(parseRoute("/apps/media")).toEqual({ kind: "missing", path: "/apps/media" });
  });
  it("round-trips app paths", () => {
    expect(parseRoute(appPath("infra", "Portainer_1"))).toEqual({ kind: "app", board: "infra", widget: "Portainer_1" });
  });
  it("round-trips board names", () => {
    expect(parseRoute(boardPath("infra-lab"))).toEqual({ kind: "board", name: "infra-lab" });
  });
  it("parses kiosk rotation settings, clamped", () => {
    expect(parseRoute("/kiosk", "?boards=home,infra&every=45")).toEqual({ kind: "kiosk", boards: ["home", "infra"], every: 45 });
    expect(parseRoute("/kiosk", "")).toEqual({ kind: "kiosk", boards: [], every: 30 });
    expect(parseRoute("/kiosk", "?every=1&boards=home,../x")).toEqual({ kind: "kiosk", boards: ["home"], every: 10 });
  });
});
