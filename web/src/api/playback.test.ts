// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { clock, livePosition, reasonWords } from "./playback";

describe("playback helpers", () => {
  const at = "2026-09-28T20:00:00Z";
  const t0 = Date.parse(at);

  it("moves a playing stream on from when it was read, never past the end", () => {
    expect(livePosition(100, 3600, at, t0 + 12_000, true)).toBe(112);
    expect(livePosition(3590, 3600, at, t0 + 30_000, true)).toBe(3600);
  });

  it("keeps a paused stream, or one with no reading time, where the server said", () => {
    expect(livePosition(100, 3600, at, t0 + 12_000, false)).toBe(100);
    expect(livePosition(100, 3600, undefined, t0 + 12_000, true)).toBe(100);
  });

  it("formats a clock", () => {
    expect(clock(754)).toBe("12:34");
    expect(clock(3723)).toBe("1:02:03");
    expect(clock(-5)).toBe("0:00");
  });

  it("turns a server's reason codes into words", () => {
    expect(reasonWords("VideoCodecNotSupported, SubtitleCodecNotSupported")).toBe(
      "video codec not supported, subtitle codec not supported",
    );
    expect(reasonWords("video, audio · hardware")).toBe("video, audio · hardware");
  });
});
