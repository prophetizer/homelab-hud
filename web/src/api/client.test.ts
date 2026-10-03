// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { fromGate } from "./client";

const res = (status: number, marked: boolean, type: ResponseType = "basic") => ({
  type,
  status,
  headers: new Headers(marked ? { "X-HUD": "1" } : {}),
});

describe("fromGate", () => {
  it("knows the gate's redirect to its sign-in portal", () => {
    expect(fromGate(res(0, false, "opaqueredirect"))).toBe(true);
  });
  it("knows a refusal that did not come from HUD", () => {
    expect(fromGate(res(401, false))).toBe(true);
    expect(fromGate(res(403, false))).toBe(true);
  });
  it("leaves HUD's own answers to HUD", () => {
    expect(fromGate(res(401, true))).toBe(false); // sign in to HUD
    expect(fromGate(res(403, true))).toBe(false); // a permission HUD refused
    expect(fromGate(res(200, true))).toBe(false);
    expect(fromGate(res(500, false))).toBe(false); // a broken gate is an error, not a sign-in
  });
});
