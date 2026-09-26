// SPDX-License-Identifier: Apache-2.0
import { formatAge } from "../../api/format";
import type { ResolvedWidget, ResourceData } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";
import { Fields } from "./Fields";

export function ResourceWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as ResourceData;
  const r = data.resource;
  return (
    <WidgetFrame widget={widget}>
      <Fields fields={data.fields} />
      {r?.links["ui"] ? (
        <p className="widget__meta">
          <a href={r.links["ui"]} target="_blank" rel="noreferrer noopener" title={`updated ${formatAge(r.fetched_at)}`}>
            Open {r.name} ↗
          </a>
        </p>
      ) : null}
    </WidgetFrame>
  );
}
