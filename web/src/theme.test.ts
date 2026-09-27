// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { NEXT_THEME, isTheme } from "./theme";

describe("theme", () => {
  it("cycles auto → light → dark → auto", () => {
    expect(NEXT_THEME.auto).toBe("light");
    expect(NEXT_THEME.light).toBe("dark");
    expect(NEXT_THEME.dark).toBe("auto");
  });
  it("accepts only the three themes from storage", () => {
    expect(isTheme("light")).toBe(true);
    expect(isTheme("solarized")).toBe(false);
    expect(isTheme(null)).toBe(false);
  });
});
