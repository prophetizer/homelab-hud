// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import type { Me } from "../api/auth";
import { buildNavigation } from "../api/nav";
import type { BoardSummary } from "../api/types";
import { boardPath, onLinkClick } from "../router";
import { BoardView } from "./Board";
import { BrandMark } from "./BrandMark";

/** Full screen for a wall display: no sidebar, larger type, the boards in rotation. It
 *  keeps polling (and so keeps the session in use), but the session's lifetime still
 *  applies — signing in again when it ends is settings.auth.session.lifetime's business. */
export function Kiosk({
  me,
  boards,
  names,
  every,
}: {
  me: Me;
  boards: BoardSummary[] | null;
  names: string[];
  every: number;
}) {
  const dashboards = buildNavigation(boards ?? [], []).boards.map((b) => b.name);
  const list = names.length > 0 ? names : dashboards;
  const [index, setIndex] = useState(0);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    document.documentElement.classList.add("kiosk");
    return () => document.documentElement.classList.remove("kiosk");
  }, []);
  useEffect(() => {
    if (list.length < 2) return;
    const t = window.setInterval(() => setIndex((i) => (i + 1) % list.length), every * 1000);
    return () => window.clearInterval(t);
  }, [list.length, every]);
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 15_000);
    return () => window.clearInterval(t);
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") setIndex((i) => (i + 1) % Math.max(1, list.length));
      if (e.key === "ArrowLeft") setIndex((i) => (i - 1 + list.length) % Math.max(1, list.length));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [list.length]);

  const current = list[index % Math.max(1, list.length)];
  return (
    <div className="kiosk-shell">
      <header className="kiosk__bar">
        <span className="sidebar__brand">
          <BrandMark />
          HUD
        </span>
        <span className="kiosk__dots" aria-label={`board ${index + 1} of ${list.length}`}>
          {list.map((n, i) => (
            <button
              key={n}
              type="button"
              className="kiosk__dot"
              aria-current={i === index % list.length ? "true" : undefined}
              title={n}
              onClick={() => setIndex(i)}
            />
          ))}
        </span>
        <span className="kiosk__clock">
          {now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          <span className="kiosk__date">{now.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}</span>
        </span>
        {current ? (
          <a className="kiosk__exit" href={boardPath(current)} onClick={onLinkClick}>
            Exit
          </a>
        ) : null}
      </header>
      <main className="main kiosk__main">
        {current ? <BoardView key={current} name={current} me={me} /> : <div className="board-status">No boards to show.</div>}
      </main>
    </div>
  );
}
