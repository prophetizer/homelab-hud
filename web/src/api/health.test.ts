// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { formatBytes, formatUptime } from "./health";

describe("formatBytes", () => {
  it("uses binary units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(1024)).toBe("1.0 KiB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MiB");
  });
});

describe("formatUptime", () => {
  it("picks the two most significant units", () => {
    expect(formatUptime(59)).toBe("0m 59s");
    expect(formatUptime(3600 + 120)).toBe("1h 2m");
    expect(formatUptime(2 * 86400 + 3 * 3600)).toBe("2d 3h");
  });
});
