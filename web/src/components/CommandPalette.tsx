// SPDX-License-Identifier: Apache-2.0
import { useEffect, useMemo, useRef, useState } from "react";
import { fetchBoard } from "../api/client";
import { fuzzyScore } from "../api/fuzzy";
import { buildNavigation } from "../api/nav";
import type { App, BoardSummary, ListData } from "../api/types";
import { appPath, boardPath, navigate } from "../router";
import { Icon } from "./widgets/Icon";

interface Entry {
  key: string;
  title: string;
  kind: "Board" | "App" | "Link" | "Page";
  icon: string | null;
  go: () => void;
}

/** Open from anywhere: Ctrl/⌘-K, or "/" when not typing. */
export function openPalette(): void {
  window.dispatchEvent(new Event("hud:palette"));
}

const openExternal = (url: string) => () => window.open(url, "_blank", "noopener,noreferrer");

/** Ctrl-K: every board, app and link card, fuzzy-matched. Link cards come from the
 *  dashboard boards, fetched once when the palette first opens. */
export function CommandPalette({ boards, apps }: { boards: BoardSummary[] | null; apps: App[] | null }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [links, setLinks] = useState<Entry[] | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const nav = buildNavigation(boards ?? [], apps ?? []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && e.target.closest("input, textarea, [contenteditable]");
      if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing && !open)) {
        e.preventDefault();
        setOpen(true);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("hud:palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("hud:palette", onOpen);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    input.current?.focus();
    if (links !== null) return;
    const ctl = new AbortController();
    Promise.all(nav.boards.map((b) => fetchBoard(b.name, ctl.signal).catch(() => null)))
      .then((resolved) => {
        const found: Entry[] = [];
        for (const board of resolved) {
          for (const w of board?.widgets ?? []) {
            const data = w.data as unknown as ListData;
            if (w.type !== "list" || data.layout !== "cards") continue;
            for (const item of data.items ?? []) {
              const url = item.links["ui"];
              if (url) found.push({ key: `link:${item.uid}`, title: item.title, kind: "Link", icon: item.icon ?? null, go: openExternal(url) });
            }
          }
        }
        setLinks(found);
      })
      .catch(() => setLinks([]));
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const entries = useMemo<Entry[]>(() => {
    const out: Entry[] = nav.boards.map((b) => ({
      key: `board:${b.name}`,
      title: b.title,
      kind: "Board",
      icon: b.icon ? b.icon.replace(/^mdi:/, "mdi-") : null,
      go: () => navigate(boardPath(b.name)),
    }));
    out.push({ key: "page:system", title: "System", kind: "Page", icon: "mdi-heart-pulse", go: () => navigate("/system") });
    for (const g of nav.appGroups) {
      for (const a of g.apps) {
        const external = a.framing.allowed !== true && a.fallback === "new_tab";
        out.push({
          key: `app:${a.board}/${a.widget}`,
          title: a.title,
          kind: "App",
          icon: a.icon ?? null,
          go: external ? openExternal(a.url) : () => navigate(appPath(a.board, a.widget)),
        });
      }
    }
    return [...out, ...(links ?? [])];
  }, [nav.boards, nav.appGroups, links]);

  const results = useMemo(() => {
    const scored = entries
      .map((e) => ({ e, s: fuzzyScore(query, e.title) }))
      .filter((x): x is { e: Entry; s: number } => x.s !== null);
    if (query.trim()) scored.sort((a, b) => b.s - a.s);
    return scored.slice(0, 50).map((x) => x.e);
  }, [entries, query]);

  if (!open) return null;
  const choose = (e: Entry | undefined) => {
    if (!e) return;
    setOpen(false);
    e.go();
  };
  return (
    <div className="palette" role="dialog" aria-modal="true" aria-label="Go to" onClick={() => setOpen(false)}>
      <div className="palette__panel" onClick={(e) => e.stopPropagation()}>
        <input
          ref={input}
          className="palette__input"
          placeholder="Go to a board, app or link…"
          value={query}
          aria-controls="palette-results"
          aria-activedescendant={results[active] ? `palette-${active}` : undefined}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((i) => Math.min(i + 1, results.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((i) => Math.max(i - 1, 0));
            } else if (e.key === "Enter") {
              choose(results[active]);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
        />
        <ul className="palette__results" id="palette-results" role="listbox">
          {results.map((r, i) => (
            <li
              key={r.key}
              id={`palette-${i}`}
              role="option"
              aria-selected={i === active}
              className="palette__item"
              onPointerEnter={() => setActive(i)}
              onClick={() => choose(r)}
            >
              <Icon name={r.icon} title={r.title} size="sm" />
              <span className="palette__title">{r.title}</span>
              <span className="palette__kind">{r.kind}</span>
            </li>
          ))}
          {results.length === 0 ? <li className="palette__none">No match</li> : null}
          {links === null ? <li className="palette__none">Gathering links…</li> : null}
        </ul>
      </div>
    </div>
  );
}
