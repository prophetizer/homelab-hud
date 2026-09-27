// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { firstName, greeting, hourIn, searchHref, temperature, windSpeed } from "./header";

describe("header helpers", () => {
  it("greets by the hour", () => {
    expect([3, 8, 13, 19, 23].map(greeting)).toEqual([
      "Good night",
      "Good morning",
      "Good afternoon",
      "Good evening",
      "Good night",
    ]);
  });

  it("reads the hour in the instance's timezone, not the browser's", () => {
    const noonUtc = new Date("2026-09-27T12:00:00Z");
    expect(hourIn("UTC", noonUtc)).toBe(12);
    expect(hourIn("America/Chicago", noonUtc)).toBe(7);
    expect(hourIn("Not/AZone", noonUtc)).toBe(noonUtc.getHours());
  });

  it("uses the first word of a display name", () => {
    expect(firstName("  Ada Lovelace ")).toBe("Ada");
  });

  it("encodes the query into the engine's template and refuses non-http templates", () => {
    expect(searchHref("https://duckduckgo.com/?q={q}", " a&b c ")).toBe("https://duckduckgo.com/?q=a%26b%20c");
    expect(searchHref("javascript:alert({q})", "x")).toBeNull();
    expect(searchHref("https://example.test/", "x")).toBeNull();
  });

  it("shows Celsius readings in the provider's display units", () => {
    expect(temperature(21.4, "metric")).toBe("21°");
    expect(temperature(21.4, "imperial")).toBe("71°");
    expect(temperature(null, "metric")).toBe("—");
    expect(windSpeed(16.1, "imperial")).toBe("10 mph");
    expect(windSpeed(12.3, undefined)).toBe("12 km/h");
  });
});
