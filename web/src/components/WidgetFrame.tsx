// SPDX-License-Identifier: Apache-2.0
import type { ReactNode } from "react";
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
export function WidgetFrame({ widget, children, className }: Props) {
  const title = widget.title ?? widget.id;
  return (
    <section
      className={`widget widget--${widget.type}${className ? ` ${className}` : ""}`}
      aria-label={title}
      data-stale={widget.stale || undefined}
    >
      <h2 className="widget__title">
        {widget.state ? <span className="status-dot" data-state={widget.state} aria-label={widget.state} /> : null}
        <Icon name={widget.icon} title={title} size="sm" fallback="none" />
        <span className="widget__title-text">{title}</span>
        {widget.stale ? (
          <span className="badge" title="last known good; the provider is failing">
            stale
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
