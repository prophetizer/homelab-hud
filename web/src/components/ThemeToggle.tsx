// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { NEXT_THEME, THEME_LABEL, currentTheme, setTheme, themeParkActive } from "../theme";

/** Cycles auto → light → dark for this browser only. Hidden while the instance wears a
 *  theme.park palette, which it would not change. */
export function ThemeToggle() {
  const [theme, set] = useState(currentTheme);
  if (themeParkActive()) return null;
  return (
    <button
      className="sidebar__theme"
      type="button"
      title={`Theme: ${THEME_LABEL[theme]} (click for ${THEME_LABEL[NEXT_THEME[theme]]})`}
      onClick={() => {
        const next = NEXT_THEME[theme];
        setTheme(next);
        set(next);
      }}
    >
      {THEME_LABEL[theme]}
    </button>
  );
}
