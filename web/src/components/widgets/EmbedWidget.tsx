// SPDX-License-Identifier: Apache-2.0
import type { EmbedData, ResolvedWidget } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";

// PLAN §8.4: every iframe is sandboxed and sends no referrer; the page CSP's frame-src
// allowlist (generated from config) is the second fence.
const SANDBOX = {
  strict: "allow-scripts allow-same-origin allow-forms allow-popups",
  relaxed: "allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads",
} as const;

export function EmbedWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as EmbedData;
  const title = widget.title ?? widget.id;
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
