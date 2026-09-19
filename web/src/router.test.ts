// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { boardPath, parseRoute } from "./router";

describe("parseRoute", () => {
  it("maps paths to routes", () => {
    expect(parseRoute("/")).toEqual({ kind: "overview" });
    expect(parseRoute("")).toEqual({ kind: "overview" });
    expect(parseRoute("/boards/media")).toEqual({ kind: "board", name: "media" });
    expect(parseRoute("/boards/media/")).toEqual({ kind: "board", name: "media" });
    expect(parseRoute("/boards/")).toEqual({ kind: "missing", path: "/boards" });
    expect(parseRoute("/boards/a b")).toEqual({ kind: "missing", path: "/boards/a b" });
    expect(parseRoute("/nope")).toEqual({ kind: "missing", path: "/nope" });
  });
  it("round-trips board names", () => {
    expect(parseRoute(boardPath("infra-lab"))).toEqual({ kind: "board", name: "infra-lab" });
  });
});
