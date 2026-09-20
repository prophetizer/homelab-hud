// SPDX-License-Identifier: Apache-2.0
import { fetchApp } from "../api/client";
import type { App } from "../api/types";
import { usePoll } from "../hooks/usePoll";
import { SANDBOX } from "./widgets/EmbedWidget";

// Re-probe rarely: framing headers do not change often, and the pane is the app itself.
const POLL_MS = 120_000;

/**
 * The workspace pane (PLAN §8.4): one embedded app filling the main area, with the same
 * sandbox and referrer rules as an inline embed. When the app refuses framing the pane
 * is the honest card — never a grey box — and says why.
 */
export function Workspace({ board, widget }: { board: string; widget: string }) {
  const { data, error } = usePoll(`app:${board}/${widget}`, (signal) => fetchApp(board, widget, signal), POLL_MS);
  if (!data) {
    return (
      <div className="board-status" role={error ? "alert" : undefined}>
        {error ? `App ${board}/${widget}: ${error}` : "Loading…"}
      </div>
    );
  }
  return (
    <div className="workspace">
      <header className="board__header">
        <h1 className="board__title">
          {data.title} <span className="workspace__board">· {data.board_title}</span>
        </h1>
        <span className="board__meta">
          <a href={data.url} target="_blank" rel="noreferrer noopener">
            open in new tab ↗
          </a>
        </span>
      </header>
      {data.framing.allowed === true ? (
        <iframe
          className="workspace__frame"
          src={data.url}
          title={data.title}
          sandbox={SANDBOX[data.sandbox]}
          referrerPolicy="no-referrer"
        />
      ) : (
        <FallbackCard app={data} />
      )}
    </div>
  );
}

export function FallbackCard({ app }: { app: App }) {
  const blocked = app.framing.allowed === false;
  return (
    <div className="workspace__card" role="status">
      <p className="embed__fallback">
        {blocked ? "This app refuses to be embedded." : "Could not reach this app to check whether it can be embedded."}
      </p>
      <p className="workspace__reason">{app.framing.reason}</p>
      {blocked ? (
        <p className="workspace__reason">
          The remedy is at the reverse proxy for this host only: strip that header, knowing it removes a clickjacking
          protection, and keep the host LAN-only behind auth.
        </p>
      ) : null}
      <a className="button" href={app.url} target="_blank" rel="noreferrer noopener">
        Open {app.title}
      </a>
    </div>
  );
}
