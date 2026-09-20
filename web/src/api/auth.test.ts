// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { type Me, hasPermission, oidcStartUrl } from "./auth";

const me = (permissions: string[]): Me => ({
  subject: "u",
  display_name: "U",
  groups: [],
  source: "local",
  permissions,
  csrf_token: "t",
});

describe("hasPermission", () => {
  it("mirrors the server's matching rules", () => {
    expect(hasPermission(me(["*"]), "anything:at:all")).toBe(true);
    expect(hasPermission(me(["boards:view:*"]), "boards:view:media")).toBe(true);
    expect(hasPermission(me(["boards:view:*"]), "boards:edit:media")).toBe(false);
    expect(hasPermission(me(["boards:view:media"]), "boards:view:media2")).toBe(false);
    expect(hasPermission(me([]), "boards:view:media")).toBe(false);
    expect(hasPermission(null, "*")).toBe(false);
  });
});

describe("oidcStartUrl", () => {
  it("encodes the return path", () => {
    expect(oidcStartUrl("/boards/media?x=1")).toBe("/api/v1/auth/oidc/start?next=%2Fboards%2Fmedia%3Fx%3D1");
  });
});
