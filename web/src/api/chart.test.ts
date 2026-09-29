// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { seriesStyle, stack, thresholdValue, withAlpha } from "./chart";

describe("chart helpers", () => {
  it("stacks series and keeps a gap only where every series has one", () => {
    expect(
      stack([
        [1, null, null, 2],
        [3, 4, null, null],
      ]),
    ).toEqual([
      [1, 0, null, 2],
      [4, 4, null, 2],
    ]);
    expect(stack([])).toEqual([]);
  });

  it("puts a colour at an alpha", () => {
    expect(withAlpha("#e8edf2", 0.1)).toBe("rgba(232, 237, 242, 0.1)");
    expect(withAlpha("#fff", 0.5)).toBe("rgba(255, 255, 255, 0.5)");
    expect(withAlpha("rgb(10, 20, 30)", 0.2)).toBe("rgba(10, 20, 30, 0.2)");
    expect(withAlpha("currentColor", 0.2)).toBe("currentColor");
  });

  it("styles series by tone and dash, cycling", () => {
    expect(seriesStyle(0).dash).toEqual([]);
    expect(seriesStyle(1).dash).toEqual([6, 4]);
    expect(seriesStyle(6)).toEqual(seriesStyle(0));
  });

  it("reads a threshold's value", () => {
    expect(thresholdValue({ gte: 90, lte: null })).toBe(90);
    expect(thresholdValue({ gte: null, lte: 5 })).toBe(5);
  });
});
