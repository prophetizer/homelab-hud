// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { stateCounts } from "./ListWidget";

describe("stateCounts", () => {
  it("leads with the worst state", () => {
    const items = [{ state: "up" }, { state: "down" }, { state: "up" }, { state: "unknown" }] as const;
    expect(stateCounts([...items])).toBe("1 down · 1 unknown · 2 up");
  });
  it("is empty for no items", () => {
    expect(stateCounts([])).toBe("");
  });
});
