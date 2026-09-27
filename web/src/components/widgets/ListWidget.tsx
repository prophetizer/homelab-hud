// SPDX-License-Identifier: Apache-2.0
import type { FieldValue, ListData, ResolvedWidget } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";
import { Meter } from "./Meter";
import { fieldText } from "./Fields";

/**
 * A row's fields, arranged for reading: text (a description, a status message) goes on a
 * second line under the name; numbers go right, in aligned columns; state is only spelled
 * out when it is not "up" — the dot already says up, and quiet-when-fine is what makes
 * the loud rows stand out (invariant 6).
 */
export function splitFields(fields: readonly FieldValue[]): { sub: FieldValue[]; values: FieldValue[]; state: string | null } {
  const sub: FieldValue[] = [];
  const values: FieldValue[] = [];
  let state: string | null = null;
  for (const f of fields) {
    if (f.key === "name") continue; // the row label already is the name
    if (f.key === "state") {
      state = f.value === "up" ? null : String(f.value);
    } else if (f.unit !== null || typeof f.value === "number") {
      values.push(f);
    } else if (f.value !== null && f.value !== undefined && f.value !== "") {
      sub.push(f);
    }
  }
  return { sub, values, state };
}

export function ListWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as ListData;
  return (
    <WidgetFrame widget={widget}>
      {data.items.length === 0 ? (
        <p className="list__empty">{data.empty_text}</p>
      ) : (
        <ul className="list">
          {data.items.map((item) => {
            const { sub, values, state } = splitFields(item.fields.filter((f) => String(f.value) !== item.title));
            return (
              <li key={item.uid} className="list__item" data-stale={item.stale || undefined}>
                <span className="status-dot" data-state={item.state} aria-label={item.state} />
                <div className="list__main">
                  {item.links["ui"] ? (
                    <a className="list__name" href={item.links["ui"]} target="_blank" rel="noreferrer noopener">
                      {item.title}
                    </a>
                  ) : (
                    <span className="list__name">{item.title}</span>
                  )}
                  {sub.length > 0 ? <span className="list__sub">{sub.map(fieldText).join(" · ")}</span> : null}
                  {item.bar !== undefined && item.bar !== null ? (
                    <span className="list__bar">
                      <Meter pct={item.bar} state={item.state} label={`${item.bar.toFixed(0)} %`} />
                    </span>
                  ) : null}
                </div>
                {/* The state never shrinks: it is the loud part of a row that is not fine. */}
                {state ? (
                  <span className="list__field list__state" data-state={state}>
                    {state}
                  </span>
                ) : null}
                {values.length > 0 ? (
                  <div className="list__values">
                    {values.map((f) => (
                      <span key={f.key} className="list__field" title={f.label}>
                        {fieldText(f)}
                      </span>
                    ))}
                  </div>
                ) : null}
              </li>
            );
          })}
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
