// SPDX-License-Identifier: Apache-2.0
// A handful of routes do not justify a router dependency. Path-based so deep links survive a
// reload (FastAPI serves index.html for any non-API path).
import { useEffect, useState } from "react";

export type Route =
  | { kind: "home" } // "/" — lands on the Home board (see api/nav.ts landingBoard)
  | { kind: "system" } // provider and process health
  | { kind: "board"; name: string }
  | { kind: "app"; board: string; widget: string }
  | { kind: "missing"; path: string };

export function parseRoute(pathname: string): Route {
  const path = pathname.replace(/\/+$/, "") || "/";
  if (path === "/") return { kind: "home" };
  if (path === "/system") return { kind: "system" };
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
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.pathname));
  useEffect(() => {
    const onPop = () => setRoute(parseRoute(window.location.pathname));
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
