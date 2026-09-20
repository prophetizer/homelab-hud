// SPDX-License-Identifier: Apache-2.0
import { type CSSProperties, useState } from "react";
import { type Me, hasPermission } from "../api/auth";
import { fetchBoard } from "../api/client";
import { formatAge } from "../api/format";
import type { ResolvedBoard } from "../api/types";
import { useColumns } from "../hooks/useColumns";
import { usePoll } from "../hooks/usePoll";
import { LayoutEditor } from "./LayoutEditor";
import { Widget } from "./Widget";

const POLL_MS = 10_000;

export function BoardView({ name, me }: { name: string; me: Me }) {
  const { data, error, fetchedAt, refresh, accept } = usePoll(
    `board:${name}`,
    (signal) => fetchBoard(name, signal),
    POLL_MS,
  );
  const [editing, setEditing] = useState(false);
  const { placed } = useColumns(data?.layout ?? { columns: { sm: 1, md: 2, lg: 4 }, gap: 12 });
  if (!data) {
    return (
      <div className="board-status" role={error ? "alert" : undefined}>
        {error ? `Board "${name}": ${error}` : "Loading…"}
      </div>
    );
  }
  // Editing is on the lg grid only: on narrower viewports the board reflows and a drag
  // would not mean what it looks like.
  const canEdit = placed && hasPermission(me, `boards:edit:${name}`);
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
        </span>
      </header>
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
        />
      ) : (
        <BoardGrid board={data} />
      )}
    </>
  );
}

// Explicit lg placement from grid:{col,row,w,h}; smaller breakpoints reflow in source
// order with spans clamped to the column count (react-grid-layout comes with the editor).
export function BoardGrid({ board }: { board: ResolvedBoard }) {
  const { columns, placed } = useColumns(board.layout);
  const style: CSSProperties = {
    gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
    gap: `${board.layout.gap}px`,
  };
  return (
    <div className="board board--grid" style={style}>
      {board.widgets.map((w) => {
        const span = Math.min(w.grid.w, columns);
        const cell: CSSProperties = placed
          ? { gridColumn: `${Math.min(w.grid.col, columns)} / span ${span}`, gridRow: `${w.grid.row} / span ${w.grid.h}` }
          : { gridColumn: `span ${span}`, gridRow: `span ${w.grid.h}` };
        return (
          <div key={w.id} className="board__cell" style={cell}>
            <Widget widget={w} board={board.name} />
          </div>
        );
      })}
      {board.widgets.length === 0 ? <p className="board-status">This board has no widgets.</p> : null}
    </div>
  );
}
