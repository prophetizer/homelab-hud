// SPDX-License-Identifier: Apache-2.0
import { type CSSProperties, useCallback, useEffect, useState } from "react";
import { type Me, hasPermission } from "../api/auth";
import { fetchBoard, fetchExpanded } from "../api/client";
import { formatAge, formatValue } from "../api/format";
import { sectionGroups } from "../api/layout";
import type { Problem, ResolvedBoard, ResolvedSection, ResolvedWidget, State } from "../api/types";
import { DetailContext } from "./detail";
import { useColumns } from "../hooks/useColumns";
import { usePoll } from "../hooks/usePoll";
import { LayoutEditor } from "./LayoutEditor";
import { type BuilderMode, WidgetBuilder } from "./WidgetBuilder";
import { Widget } from "./Widget";

const POLL_MS = 10_000;

export function BoardView({ name, me }: { name: string; me: Me }) {
  const { data, error, fetchedAt, refresh, accept } = usePoll(
    `board:${name}`,
    (signal) => fetchBoard(name, signal),
    POLL_MS,
  );
  const [editing, setEditing] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);
  const [builder, setBuilder] = useState<BuilderMode | null>(null);
  const closeBuilder = useCallback(() => setBuilder(null), []);
  const closeDetail = useCallback(() => setDetail(null), []);
  const { placed } = useColumns(data?.layout ?? { columns: { sm: 1, md: 2, lg: 4 }, gap: 12 });
  if (!data) {
    return error ? (
      <div className="board-status" role="alert">
        Board "{name}": {error}
      </div>
    ) : (
      <BoardSkeleton />
    );
  }
  // Editing is on the lg grid only: on narrower viewports the board reflows and a drag
  // would not mean what it looks like.
  const canEdit = placed && hasPermission(me, `boards:edit:${name}`);
  // The builder lists everything HUD collects, so it also needs resources:view.
  const canBuild = canEdit && hasPermission(me, "resources:view");
  const addAt = canBuild ? (section: string | null) => setBuilder({ kind: "add", section }) : undefined;
  return (
    <>
      <header className="board__header">
        <h1 className="board__title">{data.title}</h1>
        <span className="board__meta">
          {error ? <span className="board__error">refresh failed: {error} · </span> : null}
          updated {formatAge(fetchedAt?.toISOString() ?? null)}
          {canEdit && !editing ? (
            <>
              {" · "}
              <button className="board__edit" type="button" onClick={() => setEditing(true)}>
                Edit layout
              </button>
            </>
          ) : null}
          {addAt && !editing ? (
            <>
              {" · "}
              <button className="board__edit" type="button" onClick={() => addAt(null)}>
                + Add widget
              </button>
            </>
          ) : null}
        </span>
      </header>
      <SummaryBar board={data} />
      {editing ? (
        <LayoutEditor
          key={data.revision}
          board={data}
          onSaved={(fresh) => {
            accept(fresh);
            setEditing(false);
          }}
          onCancel={() => setEditing(false)}
          onReload={() => {
            refresh();
            setEditing(false);
          }}
          onEdit={canBuild ? (widget) => setBuilder({ kind: "edit", widget }) : undefined}
        />
      ) : (
        <DetailContext.Provider value={setDetail}>
          <BoardGrid board={data} onAdd={addAt} />
        </DetailContext.Provider>
      )}
      {detail ? <DetailOverlay board={name} widget={detail} onClose={closeDetail} /> : null}
      {builder ? (
        <WidgetBuilder
          key={builder.kind === "edit" ? builder.widget : `add:${builder.section ?? ""}`}
          board={data}
          mode={builder}
          onClose={closeBuilder}
          onDone={(fresh) => {
            accept(fresh);
            setBuilder(null);
          }}
        />
      ) : null}
    </>
  );
}

/** Where tiles will be, while the board loads: shapes, not a word. */
function BoardSkeleton() {
  return (
    <div className="board board--grid board--skeleton" aria-busy="true" aria-label="Loading board">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="skeleton-tile" />
      ))}
    </div>
  );
}

/** A tile, large: every row, a day of trend. Esc or the backdrop closes it. */
function DetailOverlay({ board, widget, onClose }: { board: string; widget: string; onClose: () => void }) {
  const [resolved, setResolved] = useState<ResolvedWidget | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    fetchExpanded(board, widget, ctl.signal)
      .then(setResolved)
      .catch((e: unknown) => {
        if (!ctl.signal.aborted) setFailed(e instanceof Error ? e.message : String(e));
      });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      ctl.abort();
      window.removeEventListener("keydown", onKey);
    };
  }, [board, widget, onClose]);
  return (
    <div className="detail" role="dialog" aria-modal="true" aria-label="Tile detail" onClick={onClose}>
      <div className="detail__panel" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="detail__close" onClick={onClose} aria-label="Close">
          ×
        </button>
        {resolved ? (
          <Widget widget={resolved} board={board} />
        ) : (
          <p className="board-status" role={failed ? "alert" : "status"}>
            {failed ?? "Loading…"}
          </p>
        )}
      </div>
    </div>
  );
}

const WORST_FIRST: State[] = ["down", "degraded", "unknown", "paused"];

function jump(id: string | null | undefined) {
  if (!id) return;
  const el = document.getElementById(`widget-${id}`);
  el?.scrollIntoView({ behavior: "smooth", block: "center" });
  el?.classList.add("board__cell--flash");
  window.setTimeout(() => el?.classList.remove("board__cell--flash"), 1600);
}

const VERDICT_NAMES = 3;
const SAYS: Record<string, string> = { down: "is down", degraded: "is degraded", unknown: "is not reporting" };

/** The board's one sentence: "2 things need attention — Portainer is down · Bazarr is
 *  degraded", each name jumping to its tile; or a quiet "All clear". */
export function Verdict({ problems, total }: { problems: Problem[]; total: number }) {
  if (total === 0) return null;
  if (problems.length === 0) {
    return (
      <p className="verdict" data-state="up">
        <span className="status-dot" data-state="up" />
        <b>All clear</b>
      </p>
    );
  }
  const worst = problems[0]?.state ?? "degraded";
  return (
    <p className="verdict" data-state={worst} role="status">
      <span className="status-dot" data-state={worst} />
      <b>
        {problems.length === 1 ? "1 thing needs" : `${problems.length} things need`} attention
      </b>
      <span className="verdict__items">
        {problems.slice(0, VERDICT_NAMES).map((p, i) => (
          <span key={p.uid}>
            {i > 0 ? " · " : null}
            <button type="button" className="verdict__name" onClick={() => jump(p.widget)} disabled={!p.widget}>
              {p.name}
            </button>{" "}
            {SAYS[p.state] ?? p.state}
          </span>
        ))}
        {problems.length > VERDICT_NAMES ? ` · +${problems.length - VERDICT_NAMES} more` : null}
      </span>
    </p>
  );
}

/** "142 resources · 1 down · 3 degraded · 2 tiles failing". Each problem count jumps to the
 *  first tile in that state; a board with nothing wrong gets one quiet line. */
export function SummaryBar({ board }: { board: ResolvedBoard }) {
  const counts = board.summary ?? {};
  const total = Object.values(counts).reduce((a, b) => a + (b ?? 0), 0);
  const failing = board.widgets.filter((w) => w.error);
  if (total === 0 && failing.length === 0) return null;
  return (
    <nav className="board__summary" aria-label="Board status">
      {board.problems ? <Verdict problems={board.problems} total={total} /> : null}
      <span className="board__summary-total">
        <span className="status-dot" data-state={WORST_FIRST.find((s) => counts[s]) ?? "up"} />
        {total} {total === 1 ? "resource" : "resources"}
      </span>
      {WORST_FIRST.filter((s) => counts[s]).map((s) => (
        <button
          key={s}
          type="button"
          className="board__summary-chip"
          data-state={s}
          onClick={() => jump(board.widgets.find((w) => w.state === s)?.id)}
        >
          {counts[s]} {s}
        </button>
      ))}
      {failing.length > 0 ? (
        <button type="button" className="board__summary-chip" data-state="down" onClick={() => jump(failing[0]?.id)}>
          {failing.length} {failing.length === 1 ? "tile" : "tiles"} failing
        </button>
      ) : null}
      {counts.up ? <span className="board__summary-up">{counts.up} up</span> : null}
    </nav>
  );
}

/** A section's heading band: its title, a rule, and its readings ("3 streaming"). */
function SectionHead({ section, onAdd }: { section: ResolvedSection; onAdd?: ((section: string) => void) | undefined }) {
  const readings = section.stats.filter((s) => s.value !== null);
  return (
    <div className="board-section__head">
      <h2 className="board-section__title">{section.title}</h2>
      <span className="board-section__rule" />
      {readings.length > 0 ? (
        <span className="board-section__sum">
          {readings.map((s, i) => (
            <span key={s.label} data-state={s.state}>
              {i > 0 ? " · " : null}
              {formatValue(s.value, s.unit, s.unit === "pct" ? 0 : 1)} {s.label}
            </span>
          ))}
        </span>
      ) : null}
      {onAdd ? (
        <button type="button" className="board__edit board-section__add" onClick={() => onAdd(section.id)}>
          + Add here
        </button>
      ) : null}
    </div>
  );
}

export function BoardGrid({ board, onAdd }: { board: ResolvedBoard; onAdd?: ((section: string | null) => void) | undefined }) {
  const groups = sectionGroups(board);
  if (groups.length === 1 && groups[0]?.section === null) return <TileGrid board={board} widgets={board.widgets} />;
  return (
    <>
      {groups.map((g) =>
        g.section ? (
          <section key={g.section.id} className="board-section" aria-label={g.section.title}>
            <SectionHead section={g.section} onAdd={onAdd} />
            <TileGrid board={board} widgets={g.widgets} />
          </section>
        ) : (
          <TileGrid key="" board={board} widgets={g.widgets} />
        ),
      )}
    </>
  );
}

// Explicit lg placement from grid:{col,row,w,h} (rows count from the top of the tile's
// section); smaller breakpoints reflow in source order with spans clamped to the column
// count (react-grid-layout comes with the editor).
function TileGrid({ board, widgets }: { board: ResolvedBoard; widgets: ResolvedWidget[] }) {
  const { columns, placed } = useColumns(board.layout);
  const style: CSSProperties = {
    gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
    gap: `${board.layout.gap}px`,
  };
  return (
    <div
      className={["board board--grid", placed ? "" : "board--flow", columns === 1 ? "board--stacked" : ""].join(" ").trim()}
      style={style}
    >
      {widgets.map((w) => {
        const span = Math.min(w.grid.w, columns);
        // Reflowed (below lg): tiles take their content's height instead of the desktop row
        // span, which left short lists in tall empty boxes. An embed has no content height,
        // so it keeps its span.
        const rows = !placed && w.type !== "embed" ? "auto" : `span ${w.grid.h}`;
        const cell: CSSProperties = placed
          ? { gridColumn: `${Math.min(w.grid.col, columns)} / span ${span}`, gridRow: `${w.grid.row} / span ${w.grid.h}` }
          : { gridColumn: `span ${span}`, gridRow: rows };
        return (
          <div key={w.id} id={`widget-${w.id}`} className="board__cell" style={cell}>
            <Widget widget={w} board={board.name} />
          </div>
        );
      })}
      {widgets.length === 0 ? <p className="board-status">This board has no widgets.</p> : null}
    </div>
  );
}
