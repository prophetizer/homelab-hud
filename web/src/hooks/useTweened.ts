// SPDX-License-Identifier: Apache-2.0
import { useEffect, useRef, useState } from "react";

const DURATION_MS = 600;

function reducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

/** The value, rolling from the previous one over ~0.6 s when it changes: a number that
 *  moves says "this just updated". Instant on first render, for null, and when the user
 *  asks for reduced motion. */
export function useTweened(target: number | null): number | null {
  const [shown, setShown] = useState(target);
  const from = useRef(target);
  useEffect(() => {
    const start = from.current;
    from.current = target;
    if (target === null || start === null || start === target || reducedMotion()) {
      setShown(target);
      return;
    }
    let frame = 0;
    const t0 = performance.now();
    const step = (now: number) => {
      const k = Math.min(1, (now - t0) / DURATION_MS);
      const eased = 1 - (1 - k) ** 3;
      setShown(start + (target - start) * eased);
      if (k < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target]);
  return shown;
}
