// SPDX-License-Identifier: Apache-2.0
// Light, dark or follow the system. The instance default (settings.theme) arrives on
// <html data-theme> from the server; a viewer's own choice lives in their browser and
// wins. Status colours are the same in every theme (invariant 9).

export type Theme = "auto" | "light" | "dark";

const KEY = "hud.theme";
export const NEXT_THEME: Record<Theme, Theme> = { auto: "light", light: "dark", dark: "auto" };
export const THEME_LABEL: Record<Theme, string> = { auto: "Auto", light: "Light", dark: "Dark" };

export function isTheme(v: unknown): v is Theme {
  return v === "auto" || v === "light" || v === "dark";
}

export function currentTheme(): Theme {
  const v = document.documentElement.dataset["theme"];
  return isTheme(v) ? v : "dark";
}

export function setTheme(t: Theme): void {
  document.documentElement.dataset["theme"] = t;
  try {
    localStorage.setItem(KEY, t);
  } catch {
    // private mode or blocked storage: the choice lasts until the tab closes
  }
}

/** Before first render: a stored choice overrides the instance default. */
export function applyStoredTheme(): void {
  try {
    const v = localStorage.getItem(KEY);
    if (isTheme(v)) document.documentElement.dataset["theme"] = v;
  } catch {
    // no storage: keep the server's default
  }
}
