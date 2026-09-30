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

/** settings.theme_park is set: HUD wears a theme.park palette (surfaces and text), so the
 *  viewer's dark/light choice has nothing to change. */
export function themeParkActive(): boolean {
  return "themepark" in document.documentElement.dataset;
}

const THEMEPARK_REFRESH_MS = 60_000;

/** Fetch the palette again every minute, so a theme changed in the stack's picker reaches an
 *  open tab without a reload. Swapping the href only restyles once the new sheet is in. */
export function watchThemePark(): void {
  if (!themeParkActive()) return;
  window.setInterval(() => {
    const link = document.getElementById("themepark");
    if (link instanceof HTMLLinkElement) link.href = `/api/v1/theme.css?t=${Date.now()}`;
  }, THEMEPARK_REFRESH_MS);
}
