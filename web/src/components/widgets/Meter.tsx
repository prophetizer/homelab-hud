// SPDX-License-Identifier: Apache-2.0
import type { State } from "../../api/types";

/** A usage bar. Filled in the status colour of what it measures (invariant 9): a disk at
 *  47 % is "up" green, at 92 % whatever state its provider or thresholds gave it. */
export function Meter({ pct, state, label }: { pct: number; state: State | null; label?: string }) {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <span
      className="meter"
      data-state={state ?? undefined}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped)}
      aria-label={label}
    >
      <span className="meter__fill" style={{ width: `${clamped}%` }} />
    </span>
  );
}
