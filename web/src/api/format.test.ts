// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { formatAge, formatDuration, formatValue } from "./format";

describe("formatValue", () => {
  it("scales bytes in binary and bps in decimal bits", () => {
    expect(formatValue(0, "bytes")).toBe("0 B");
    expect(formatValue(1536, "bytes")).toBe("1.5 KiB");
    expect(formatValue(3 * 1024 ** 3, "bytes", 2)).toBe("3.00 GiB");
    expect(formatValue(12_500_000, "bps")).toBe("12.5 Mbit/s");
    expect(formatValue(-2048, "bytes")).toBe("-2.0 KiB");
  });
  it("formats the other canonical units", () => {
    expect(formatValue(42.456, "pct")).toBe("42.5 %");
    expect(formatValue(21.5, "celsius")).toBe("21.5 °C");
    expect(formatValue(412, "watts", 0)).toBe("412 W");
    expect(formatValue(3720, "seconds")).toBe("1h 2m");
    expect(formatValue(120, "count")).toBe("120");
    expect(formatValue(2.5, "count")).toBe("2.5");
  });
  it("handles non-numbers honestly", () => {
    expect(formatValue(null, "bytes")).toBe("—");
    expect(formatValue(undefined, null)).toBe("—");
    expect(formatValue(NaN, "pct")).toBe("—");
    expect(formatValue("4.0.9", null)).toBe("4.0.9");
    expect(formatValue(true, null)).toBe("yes");
    expect(formatValue(["8080->8080/tcp", "443/tcp"], null)).toBe("8080->8080/tcp, 443/tcp");
    expect(formatValue({ a: 1 }, null)).toBe('{"a":1}');
  });
});

describe("formatDuration / formatAge", () => {
  it("picks two units", () => {
    expect(formatDuration(59)).toBe("59s");
    expect(formatDuration(61)).toBe("1m 1s");
    expect(formatDuration(2 * 86400 + 3 * 3600)).toBe("2d 3h");
  });
  it("describes age relative to now", () => {
    const now = Date.parse("2026-09-19T12:00:00Z");
    expect(formatAge("2026-09-19T11:59:58Z", now)).toBe("just now");
    expect(formatAge("2026-09-19T11:30:00Z", now)).toBe("30m 0s ago");
    expect(formatAge(null, now)).toBe("never");
    expect(formatAge("garbage", now)).toBe("garbage");
  });
});
