// SPDX-License-Identifier: Apache-2.0
import type { ResolvedWidget } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";

// The engine already put the reason in widget.error; the frame renders it.
export function UnsupportedWidget({ widget }: { widget: ResolvedWidget }) {
  return (
    <WidgetFrame widget={widget}>
      <p className="widget__meta">type: {widget.type}</p>
    </WidgetFrame>
  );
}
