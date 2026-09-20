// SPDX-License-Identifier: Apache-2.0
import type { EmbedData, ResolvedWidget } from "../../api/types";
import { appPath, onLinkClick } from "../../router";
import { WidgetFrame } from "../WidgetFrame";

// PLAN §8.4: every iframe is sandboxed and sends no referrer; the page CSP's frame-src
// allowlist (generated from config) is the second fence.
export const SANDBOX = {
  strict: "allow-scripts allow-same-origin allow-forms allow-popups",
  relaxed: "allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads",
} as const;

export function EmbedWidget({ widget, board }: { widget: ResolvedWidget; board?: string | undefined }) {
  const data = widget.data as unknown as EmbedData;
  const title = widget.title ?? widget.id;
  if (data.open_in === "workspace" && board) {
    // A launcher, not a second copy of the app: the workspace pane owns the iframe.
    // With fallback: new_tab a blocked app skips the pane and opens directly.
    const direct = data.framing.allowed !== true && data.fallback === "new_tab";
    return (
      <WidgetFrame widget={widget} className="widget--card">
        <p className="embed__fallback">
          {data.framing.allowed === false ? "Refuses to be embedded; opens in a new tab." : "Opens in the workspace."}
        </p>
        {direct ? (
          <a className="button" href={data.url} target="_blank" rel="noreferrer noopener">
            Open {title} ↗
          </a>
        ) : (
          <a className="button" href={appPath(board, widget.id)} onClick={onLinkClick}>
            Open {title}
          </a>
        )}
      </WidgetFrame>
    );
  }
  if (data.framing.allowed !== true) {
    // Honest card: the app refuses framing (or could not be reached). Never a grey box.
    return (
      <WidgetFrame widget={widget} className="widget--card">
        <p className="embed__fallback">
          {data.framing.allowed === false
            ? "This app refuses to be embedded."
            : "Could not reach this app to check whether it can be embedded."}
        </p>
        <a className="button" href={data.url} target="_blank" rel="noreferrer noopener">
          Open {title}
        </a>
      </WidgetFrame>
    );
  }
  return (
    <WidgetFrame widget={widget} className="widget--embed">
      <iframe
        className="embed__frame"
        src={data.url}
        title={title}
        sandbox={SANDBOX[data.sandbox]}
        referrerPolicy="no-referrer"
        loading="lazy"
      />
    </WidgetFrame>
  );
}
