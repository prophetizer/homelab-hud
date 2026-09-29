// SPDX-License-Identifier: Apache-2.0
/** Now-playing helpers: where a stream is now, as a clock, and a server's reason in words. */

/** The position ``position`` seconds as read at ``at``, moved on to ``now`` while playing,
 *  never past the end. Paused, or with no reading time, it stays where the server said. */
export function livePosition(
  position: number,
  duration: number | null,
  at: string | undefined,
  now: number,
  playing: boolean,
): number {
  const read = at ? Date.parse(at) : Number.NaN;
  const moved = playing && Number.isFinite(read) ? Math.max(0, (now - read) / 1000) : 0;
  const pos = position + moved;
  return duration && duration > 0 ? Math.min(pos, duration) : pos;
}

/** 754 → "12:34", 3723 → "1:02:03". */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** "VideoCodecNotSupported, AudioChannelsNotSupported" → "video codec not supported, audio
 *  channels not supported". Plex's reasons are words already and pass through. */
export function reasonWords(reason: string): string {
  return reason
    .split(",")
    .map((r) =>
      r
        .trim()
        .replace(/([a-z])([A-Z])/g, "$1 $2")
        .toLowerCase(),
    )
    .filter(Boolean)
    .join(", ");
}
