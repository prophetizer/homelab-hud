// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { GridLayout, type Layout as RglLayout, noCompactor, useContainerWidth } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import { ApiError, patchBoard } from "../api/client";
import { changedPlacements, sectionGroups, toCells } from "../api/layout";
import type { ResolvedBoard, ResolvedWidget } from "../api/types";
import { Widget } from "./Widget";

interface Props {
  board: ResolvedBoard;
  onSaved: (board: ResolvedBoard) => void;
  onCancel: () => void;
  onReload: () => void;
}

const ROW_PX = 120; // matches .board--grid grid-auto-rows

/**
 * Drag/resize on the `lg` grid; Save sends only the widgets that moved, with the
 * revision the board was loaded at, so a hand edit in the meantime is refused (409)
 * rather than overwritten. The editor keeps its own copy of the layout: polls that
 * land while editing must not yank tiles out from under the cursor. A board with sections
 * gets a grid per section; a tile moves within its section (its row counts from the
 * section's top) — moving it to another section is a YAML edit.
 */
export function LayoutEditor({ board, onSaved, onCancel, onReload }: Props) {
  const groups = sectionGroups(board);
  const [initial] = useState<RglLayout>(() => toCells(board));
  const [layouts, setLayouts] = useState<Record<string, RglLayout>>(() =>
    Object.fromEntries(
      groups.map((g) => [g.section?.id ?? "", initial.filter((c) => g.widgets.some((w) => w.id === c.i))]),
    ),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const pending = changedPlacements(initial, Object.values(layouts).flat());

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      onSaved(await patchBoard(board.name, board.revision, pending));
    } catch (err) {
      setConflict(err instanceof ApiError && err.status === 409);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="editor">
      <div className="editor__bar" role="toolbar" aria-label="Layout editor">
        <span className="editor__hint">
          Drag tiles to move, pull the corner to resize. {pending.length} change{pending.length === 1 ? "" : "s"}.
        </span>
        {error ? (
          <span className="editor__error" role="alert">
            {error}
            {conflict ? (
              <>
                {" "}
                <button className="editor__link" type="button" onClick={onReload}>
                  Reload board
                </button>
              </>
            ) : null}
          </span>
        ) : null}
        <button className="button" type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button className="button button--primary" type="button" onClick={() => void save()} disabled={busy || pending.length === 0 || conflict}>
          {busy ? "Saving…" : "Save layout"}
        </button>
      </div>
      {groups.map((g) => {
        const key = g.section?.id ?? "";
        return (
          <div key={key} className={g.section ? "board-section" : undefined}>
            {g.section ? (
              <div className="board-section__head">
                <h2 className="board-section__title">{g.section.title}</h2>
                <span className="board-section__rule" />
              </div>
            ) : null}
            <EditorGrid
              board={board}
              widgets={g.widgets}
              layout={layouts[key] ?? []}
              onChange={(next) => setLayouts((all) => ({ ...all, [key]: next }))}
            />
          </div>
        );
      })}
    </div>
  );
}

function EditorGrid({
  board,
  widgets,
  layout,
  onChange,
}: {
  board: ResolvedBoard;
  widgets: ResolvedWidget[];
  layout: RglLayout;
  onChange: (layout: RglLayout) => void;
}) {
  const { width, containerRef, mounted } = useContainerWidth();
  return (
    <div ref={containerRef} className="editor__grid">
      {mounted ? (
        <GridLayout
          width={width}
          layout={layout}
          onLayoutChange={onChange}
          compactor={noCompactor}
          gridConfig={{ cols: board.layout.columns.lg, rowHeight: ROW_PX, margin: [board.layout.gap, board.layout.gap], containerPadding: [0, 0] }}
          resizeConfig={{ handles: ["se"] }}
        >
          {widgets.map((w) => (
            <div key={w.id} className="board__cell board__cell--editing">
              <Widget widget={w} />
            </div>
          ))}
        </GridLayout>
      ) : null}
    </div>
  );
}
