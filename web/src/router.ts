// SPDX-License-Identifier: Apache-2.0
// A handful of routes do not justify a router dependency. Path-based so deep links survive a
// reload (FastAPI serves index.html for any non-API path).
import { useEffect, useState } from "react";

export type Route =
  | { kind: "home" } // "/" — lands on the Home board (see api/nav.ts landingBoard)
  | { kind: "system" } // provider and process health
  | { kind: "reports" } // scheduled reports and their files
  | { kind: "board"; name: string }
  | { kind: "app"; board: string; widget: string }
  | { kind: "kiosk"; boards: string[]; every: number } // full-screen rotation for a wall display
  | { kind: "missing"; path: string };

/** /kiosk?boards=home,infra&every=30 — no boards means every dashboard board. */
export function parseKiosk(search: string): { kind: "kiosk"; boards: string[]; every: number } {
  const q = new URLSearchParams(search);
  const boards = (q.get("boards") ?? "")
    .split(",")
    .map((b) => b.trim())
    .filter((b) => /^[A-Za-z0-9_-]+$/.test(b));
  const every = Number.parseInt(q.get("every") ?? "", 10);
  return { kind: "kiosk", boards, every: Number.isFinite(every) ? Math.min(3600, Math.max(10, every)) : 30 };
}

export function parseRoute(pathname: string, search = ""): Route {
  const path = pathname.replace(/\/+$/, "") || "/";
  if (path === "/") return { kind: "home" };
  if (path === "/kiosk") return parseKiosk(search);
  if (path === "/system") return { kind: "system" };
  if (path === "/reports") return { kind: "reports" };
  const m = /^\/boards\/([A-Za-z0-9_-]+)$/.exec(path);
  if (m && m[1] !== undefined) return { kind: "board", name: decodeURIComponent(m[1]) };
  const a = /^\/apps\/([A-Za-z0-9_-]+)\/([A-Za-z0-9_-]+)$/.exec(path);
  if (a && a[1] !== undefined && a[2] !== undefined) {
    return { kind: "app", board: decodeURIComponent(a[1]), widget: decodeURIComponent(a[2]) };
  }
  return { kind: "missing", path };
}

export function boardPath(name: string): string {
  return `/boards/${encodeURIComponent(name)}`;
}

export function appPath(board: string, widget: string): string {
  return `/apps/${encodeURIComponent(board)}/${encodeURIComponent(widget)}`;
}

/** Swap the current entry for ``path``: a landing redirect must not leave "/" in history. */
export function replaceRoute(path: string): void {
  window.history.replaceState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function navigate(path: string): void {
  if (window.location.pathname !== path) {
    window.history.pushState(null, "", path);
  }
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.pathname, window.location.search));
  useEffect(() => {
    const onPop = () => setRoute(parseRoute(window.location.pathname, window.location.search));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return route;
}

/** Intercept plain left-clicks on internal links so navigation stays client-side. */
export function onLinkClick(e: React.MouseEvent<HTMLAnchorElement>): void {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const href = e.currentTarget.getAttribute("href");
  if (!href || !href.startsWith("/") || href.startsWith("//")) return;
  e.preventDefault();
  navigate(href);
}
