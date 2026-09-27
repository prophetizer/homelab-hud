// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { BrandMark } from "./BrandMark";
import type { Me } from "../api/auth";
import { buildNavigation } from "../api/nav";
import type { App, BoardSummary } from "../api/types";
import { appPath, boardPath, onLinkClick, type Route } from "../router";
import { Icon } from "./widgets/Icon";

/** Board icons are written mdi:server in YAML; /api/v1/icons takes mdi-server. */
const iconName = (icon: string | null) => (icon ? icon.replace(/^mdi:/, "mdi-") : null);

interface Props {
  boards: BoardSummary[] | null;
  boardsError: string | null;
  apps: App[] | null;
  route: Route;
  me: Me;
  onSignOut: () => void;
}

export function Sidebar({ boards, boardsError, apps, route, me, onSignOut }: Props) {
  const nav = buildNavigation(boards ?? [], apps ?? []);
  const current = (kind: Route["kind"], name?: string) =>
    route.kind === kind && (kind !== "board" || (route.kind === "board" && route.name === name)) ? "page" : undefined;
  const currentApp = (a: App) =>
    route.kind === "app" && route.board === a.board && route.widget === a.widget ? "page" : undefined;
  const openGroup = route.kind === "app" ? route.board : null;
  // On a phone the sidebar is a one-line bar; the menu opens over the page on demand.
  const [menuOpen, setMenuOpen] = useState(false);
  const here =
    route.kind === "system"
      ? "System"
      : route.kind === "board"
        ? nav.boards.find((b) => b.name === route.name)?.title
        : route.kind === "app"
          ? nav.appGroups.flatMap((g) => g.apps).find((a) => currentApp(a))?.title
          : undefined;
  const closeOnNavigate = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest("a")) setMenuOpen(false);
  };
  return (
    <aside className="sidebar" data-open={menuOpen ? "" : undefined}>
      <div className="sidebar__bar">
        <div className="sidebar__brand">
          <BrandMark />
          HUD
        </div>
        {here ? <span className="sidebar__here">{here}</span> : null}
        <button
          className="sidebar__toggle"
          type="button"
          aria-expanded={menuOpen}
          aria-controls="sidebar-menu"
          onClick={() => setMenuOpen((o) => !o)}
        >
          {menuOpen ? "Close" : "Menu"}
        </button>
      </div>
      {/* Scrolls on its own: 60 apps must not make the whole page scroll. */}
      <div className="sidebar__scroll" id="sidebar-menu" onClick={closeOnNavigate}>
        <nav className="sidebar__nav" aria-label="Boards">
          <div className="sidebar__section">Boards</div>
          {nav.boards.map((b) => (
            <a
              key={b.name}
              href={boardPath(b.name)}
              onClick={onLinkClick}
              aria-current={current("board", b.name)}
              title={b.unsupported > 0 ? `${b.unsupported} widget(s) need a later phase` : undefined}
            >
              <Icon name={iconName(b.icon)} title={b.title} size="sm" />
              {b.title}
            </a>
          ))}
          <a href="/system" onClick={onLinkClick} aria-current={current("system")} className="sidebar__system">
            <Icon name="mdi-heart-pulse" title="System" size="sm" />
            System
          </a>
          {boards && boards.length === 0 ? (
            <p className="sidebar__hint">No boards yet — add one under /config/boards/.</p>
          ) : null}
          {boardsError ? (
            <p className="sidebar__hint sidebar__hint--error" role="alert">
              boards: {boardsError}
            </p>
          ) : null}
        </nav>
        {nav.appGroups.length > 0 ? (
          <nav className="sidebar__nav" aria-label="Apps">
            <div className="sidebar__section">Apps</div>
            {nav.appGroups.map((g) => (
              <details
                key={g.board}
                className="sidebar__group"
                open={g.board === openGroup || nav.appGroups.length === 1}
              >
                <summary className="sidebar__group-title">
                  <span>{g.title}</span>
                  <span className="sidebar__count">{g.apps.length}</span>
                </summary>
                {g.apps.map((a) =>
                  a.framing.allowed !== true && a.fallback === "new_tab" ? (
                    <a
                      key={a.widget}
                      href={a.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      title={`Opens in a new tab: ${a.framing.reason}`}
                    >
                      <Icon name={a.icon} title={a.title} size="sm" />
                      {a.title}
                      <span className="sidebar__ext" aria-label="opens in a new tab">
                        ↗
                      </span>
                    </a>
                  ) : (
                    <a
                      key={a.widget}
                      href={appPath(a.board, a.widget)}
                      onClick={onLinkClick}
                      aria-current={currentApp(a)}
                      title={a.error ?? undefined}
                      data-state={a.state}
                    >
                      <Icon name={a.icon} title={a.title} size="sm" />
                      {a.title}
                    </a>
                  ),
                )}
              </details>
            ))}
          </nav>
        ) : null}
      </div>
      <div className="sidebar__account">
        <span className="sidebar__user" title={`${me.subject} via ${me.source}`}>
          {me.display_name}
        </span>
        {me.source === "forward" ? null : (
          <button className="sidebar__signout" type="button" onClick={onSignOut}>
            Sign out
          </button>
        )}
      </div>
    </aside>
  );
}
