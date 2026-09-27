// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { fieldText } from "./Fields";

describe("fieldText", () => {
  it("shows *_at fields as an age, from epoch seconds or ISO", () => {
    const now = Math.floor(Date.now() / 1000);
    expect(fieldText({ key: "attrs.added_at", label: "added at", value: now - 7200, unit: null })).toMatch(/2h/);
    expect(fieldText({ key: "attrs.added_at", label: "added at", value: String(now - 7200), unit: null })).toMatch(/2h/);
    const iso = new Date(Date.now() - 3 * 86400_000).toISOString();
    expect(fieldText({ key: "attrs.added_at", label: "added at", value: iso, unit: null })).toMatch(/3d/);
  });
  it("leaves other fields alone", () => {
    expect(fieldText({ key: "attrs.items", label: "items", value: 1234, unit: null })).toBe("1,234");
  });
});
