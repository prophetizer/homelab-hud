// SPDX-License-Identifier: Apache-2.0
import type { ListData, ResolvedWidget } from "../../api/types";
import { fieldText } from "./Fields";
import { WidgetFrame } from "../WidgetFrame";

export function ListWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as ListData;
  return (
    <WidgetFrame widget={widget}>
      {data.items.length === 0 ? (
        <p className="list__empty">{data.empty_text}</p>
      ) : (
        <ul className="list">
          {data.items.map((item) => (
            <li key={item.uid} className="list__item" data-stale={item.stale || undefined}>
              <span className="status-dot" data-state={item.state} aria-label={item.state} />
              {item.links["ui"] ? (
                <a className="list__name" href={item.links["ui"]} target="_blank" rel="noreferrer noopener">
                  {item.name}
                </a>
              ) : (
                <span className="list__name">{item.name}</span>
              )}
              {item.fields
                .filter((f) => f.key !== "name") // the row label already is the name
                .map((f) => (
                  <span key={f.key} className="list__field" title={f.label} data-state={f.key === "state" ? String(f.value) : undefined}>
                    {fieldText(f)}
                  </span>
                ))}
            </li>
          ))}
        </ul>
      )}
      {data.total > data.items.length ? (
        <p className="widget__meta">
          {data.items.length} of {data.total}
        </p>
      ) : null}
    </WidgetFrame>
  );
}
