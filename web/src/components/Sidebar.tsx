// SPDX-License-Identifier: Apache-2.0
import type { Me } from "../api/auth";
import type { App, BoardSummary } from "../api/types";
import { appPath, boardPath, onLinkClick, type Route } from "../router";

interface Props {
  boards: BoardSummary[] | null;
  boardsError: string | null;
  apps: App[] | null;
  route: Route;
  me: Me;
  onSignOut: () => void;
}

export function Sidebar({ boards, boardsError, apps, route, me, onSignOut }: Props) {
  const current = (kind: Route["kind"], name?: string) =>
    route.kind === kind && (kind !== "board" || (route.kind === "board" && route.name === name))
      ? "page"
      : undefined;
  const currentApp = (a: App) =>
    route.kind === "app" && route.board === a.board && route.widget === a.widget ? "page" : undefined;
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">HUD</div>
      <nav className="sidebar__nav" aria-label="Boards">
        <a href="/" onClick={onLinkClick} aria-current={current("overview")}>
          Overview
        </a>
        {boards?.map((b) => (
          <a
            key={b.name}
            href={boardPath(b.name)}
            onClick={onLinkClick}
            aria-current={current("board", b.name)}
            title={b.unsupported > 0 ? `${b.unsupported} widget(s) need a later phase` : undefined}
          >
            {b.title}
          </a>
        ))}
        {boards && boards.length === 0 ? (
          <p className="sidebar__hint">No boards yet — add one under /config/boards/.</p>
        ) : null}
        {boardsError ? (
          <p className="sidebar__hint sidebar__hint--error" role="alert">
            boards: {boardsError}
          </p>
        ) : null}
      </nav>
      {apps && apps.length > 0 ? (
        <nav className="sidebar__nav" aria-label="Apps">
          <div className="sidebar__section">Apps</div>
          {apps.map((a) =>
            a.framing.allowed !== true && a.fallback === "new_tab" ? (
              <a key={`${a.board}/${a.widget}`} href={a.url} target="_blank" rel="noreferrer noopener" title={a.framing.reason}>
                {a.title} ↗
              </a>
            ) : (
              <a
                key={`${a.board}/${a.widget}`}
                href={appPath(a.board, a.widget)}
                onClick={onLinkClick}
                aria-current={currentApp(a)}
                title={a.error ?? undefined}
                data-state={a.state}
              >
                {a.title}
              </a>
            ),
          )}
        </nav>
      ) : null}
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
