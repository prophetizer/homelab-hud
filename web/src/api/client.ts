// SPDX-License-Identifier: Apache-2.0
import type { BoardSummary, ResolvedBoard } from "./types";

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal: signal ?? null, cache: "no-store" });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // not JSON; keep the status line
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export function fetchBoards(signal?: AbortSignal): Promise<{ boards: BoardSummary[] }> {
  return getJson("/api/v1/boards", signal);
}

export function fetchBoard(name: string, signal?: AbortSignal): Promise<ResolvedBoard> {
  return getJson(`/api/v1/boards/${encodeURIComponent(name)}`, signal);
}
