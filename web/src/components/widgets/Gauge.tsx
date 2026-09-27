// SPDX-License-Identifier: Apache-2.0
import type { ReactNode } from "react";
import type { State } from "../../api/types";

const R = 50;
const CIRC = 2 * Math.PI * R;
const SWEEP = 0.75; // a 270° dial, open at the bottom

/** A percentage as a dial in its status colour (invariant 9: the hue is the state). */
export function Gauge({ pct, state, children }: { pct: number; state: State | null; children: ReactNode }) {
  const clamped = Math.max(0, Math.min(100, pct));
  const arc = CIRC * SWEEP;
  return (
    <div className="gauge" data-state={state ?? undefined}>
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <circle className="gauge__track" cx="60" cy="60" r={R} strokeDasharray={`${arc} ${CIRC}`} transform="rotate(135 60 60)" />
        <circle
          className="gauge__fill"
          cx="60"
          cy="60"
          r={R}
          strokeDasharray={`${(arc * clamped) / 100} ${CIRC}`}
          transform="rotate(135 60 60)"
        />
      </svg>
      <div className="gauge__center">{children}</div>
    </div>
  );
}
