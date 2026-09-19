// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import type { Layout } from "../api/types";

const MD = "(max-width: 1100px)";
const SM = "(max-width: 700px)";

/** Which of the board's column counts applies at the current viewport width. */
export function useColumns(layout: Layout): { columns: number; placed: boolean } {
  const pick = () => {
    if (window.matchMedia(SM).matches) return { columns: layout.columns.sm, placed: false };
    if (window.matchMedia(MD).matches) return { columns: layout.columns.md, placed: false };
    return { columns: layout.columns.lg, placed: true };
  };
  const [cols, setCols] = useState(pick);
  useEffect(() => {
    const queries = [window.matchMedia(SM), window.matchMedia(MD)];
    const update = () => setCols(pick());
    queries.forEach((q) => q.addEventListener("change", update));
    update();
    return () => queries.forEach((q) => q.removeEventListener("change", update));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout.columns.sm, layout.columns.md, layout.columns.lg]);
  return cols;
}
