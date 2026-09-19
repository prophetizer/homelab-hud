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
      {r ? (
        <p className="widget__meta">
          {r.provider}:{r.kind} · fetched {formatAge(r.fetched_at)}
          {r.links["ui"] ? (
            <>
              {" · "}
              <a href={r.links["ui"]} target="_blank" rel="noreferrer noopener">
                open
              </a>
            </>
          ) : null}
        </p>
      ) : null}
    </WidgetFrame>
  );
}
