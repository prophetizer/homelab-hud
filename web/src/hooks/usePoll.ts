// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useRef, useState } from "react";

export interface Polled<T> {
  data: T | null;
  error: string | null;
  fetchedAt: Date | null;
  /** Fetch now, outside the schedule (after a write, or when the user asks). */
  refresh: () => void;
  /** Replace the payload with one obtained elsewhere, e.g. a write's response. */
  accept: (data: T) => void;
}

/**
 * Poll `load` every `intervalMs`, keeping the last good payload when a fetch fails so a
 * tile shows last-known-good with the error beside it, never a blank (invariant 6).
 * Re-runs from scratch when `key` changes.
 */
export function usePoll<T>(
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
): Polled<T> {
  type Inner = Omit<Polled<T>, "refresh" | "accept">;
  const [state, setState] = useState<Inner>({ data: null, error: null, fetchedAt: null });
  const tickRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setState({ data: null, error: null, fetchedAt: null });

    const tick = async () => {
      if (timer !== undefined) clearTimeout(timer);
      try {
        const data = await load(ctrl.signal);
        if (ctrl.signal.aborted) return;
        setState({ data, error: null, fetchedAt: new Date() });
      } catch (err) {
        if (ctrl.signal.aborted) return;
        setState((prev) => ({ ...prev, error: err instanceof Error ? err.message : String(err) }));
      }
      if (!ctrl.signal.aborted) timer = setTimeout(tick, intervalMs);
    };
    tickRef.current = () => void tick();
    void tick();

    return () => {
      ctrl.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
    // `load` is expected to be stable for a given key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, intervalMs]);

  const refresh = useCallback(() => tickRef.current(), []);
  const accept = useCallback((data: T) => setState({ data, error: null, fetchedAt: new Date() }), []);
  return { ...state, refresh, accept };
}
