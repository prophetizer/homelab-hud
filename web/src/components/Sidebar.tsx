// SPDX-License-Identifier: Apache-2.0
import type { Me } from "../api/auth";
import type { BoardSummary } from "../api/types";
import { boardPath, onLinkClick, type Route } from "../router";

interface Props {
  boards: BoardSummary[] | null;
  boardsError: string | null;
  route: Route;
  me: Me;
  onSignOut: () => void;
}

export function Sidebar({ boards, boardsError, route, me, onSignOut }: Props) {
  const current = (kind: Route["kind"], name?: string) =>
    route.kind === kind && (kind !== "board" || (route.kind === "board" && route.name === name))
      ? "page"
      : undefined;
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
