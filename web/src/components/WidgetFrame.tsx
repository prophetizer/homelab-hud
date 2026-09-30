// SPDX-License-Identifier: Apache-2.0
import { type ReactNode, useContext, useEffect, useRef, useState } from "react";
import { DetailContext } from "./detail";
import type { ResolvedWidget } from "../api/types";
import { Icon } from "./widgets/Icon";

interface Props {
  widget: ResolvedWidget;
  children: ReactNode;
  className?: string | undefined;
}

// Every widget shares this frame: title with status dot, a "stale" badge when the data
// is last-known-good, and the error rendered inside the tile in status color. A tile is
// never blank (invariant 6).
/** The state a tile just changed to, for ~1.6 s — never on first render. */
function useChanged(state: string | null): string | undefined {
  const prev = useRef(state);
  const [changed, setChanged] = useState<string | undefined>();
  useEffect(() => {
    if (prev.current === state) return;
    prev.current = state;
    setChanged(state ?? undefined);
    const t = window.setTimeout(() => setChanged(undefined), 1600);
    return () => window.clearTimeout(t);
  }, [state]);
  return changed;
}

// The header says a problem in words; "up" is the dot alone.
const STATE_WORDS: Record<string, string> = { down: "down", degraded: "degraded", unknown: "not reporting", paused: "paused" };

export function WidgetFrame({ widget, children, className }: Props) {
  const title = widget.title ?? widget.id;
  const changed = useChanged(widget.state);
  const openDetail = useContext(DetailContext);
  return (
    <section
      className={`widget widget--${widget.type}${className ? ` ${className}` : ""}`}
      aria-label={title}
      data-stale={widget.stale || undefined}
      data-changed={changed}
    >
      <h2 className="widget__title">
        <span className="widget__icon">
          <Icon name={widget.icon} title={title} size="sm" />
        </span>
        {openDetail ? (
          <button type="button" className="widget__title-text widget__open" onClick={() => openDetail(widget.id)} title="Open larger">
            {title}
          </button>
        ) : (
          <span className="widget__title-text">{title}</span>
        )}
        {widget.stale ? (
          <span className="badge" title="last known good; the provider is failing">
            stale
          </span>
        ) : null}
        {widget.state ? (
          <span className="widget__state" data-state={widget.state}>
            <span className="status-dot" data-state={widget.state} aria-label={widget.state} />
            {widget.state === "up" ? null : STATE_WORDS[widget.state] ?? widget.state}
          </span>
        ) : null}
      </h2>
      <div className="widget__body">{children}</div>
      {widget.error ? (
        <p className="widget__error" role="alert">
          {widget.error}
        </p>
      ) : null}
    </section>
  );
}
