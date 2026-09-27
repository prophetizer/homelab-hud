// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { fuzzyScore } from "./fuzzy";

describe("fuzzyScore", () => {
  it("matches characters in order, case-insensitively", () => {
    expect(fuzzyScore("snr", "Sonarr")).not.toBeNull();
    expect(fuzzyScore("rns", "Sonarr")).toBeNull();
  });
  it("prefers prefixes and word starts", () => {
    const ranked = ["Home Assistant", "Homarr", "Chrome"].sort(
      (a, b) => (fuzzyScore("hom", b) ?? -1) - (fuzzyScore("hom", a) ?? -1),
    );
    expect(ranked[2]).toBe("Chrome");
    expect((fuzzyScore("ha", "Home Assistant") ?? 0) > (fuzzyScore("ha", "Chat") ?? 0)).toBe(true);
  });
  it("treats an empty query as matching everything", () => {
    expect(fuzzyScore("", "anything")).toBe(0);
  });
});
