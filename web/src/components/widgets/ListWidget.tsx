// SPDX-License-Identifier: Apache-2.0
import type { FieldValue, ListData, ResolvedWidget, State } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";
import { Icon } from "./Icon";
import { Meter } from "./Meter";
import { Poster } from "./Poster";
import { valueParts } from "./MetricWidget";
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

const STATE_ORDER: State[] = ["down", "degraded", "unknown", "paused", "up"];

/** "3 down · 2 unknown · 112 up": worst first, so the count that matters leads. */
export function stateCounts(items: { state: State }[]): string {
  const counts = new Map<State, number>();
  for (const i of items) counts.set(i.state, (counts.get(i.state) ?? 0) + 1);
  return STATE_ORDER.filter((s) => counts.has(s))
    .map((s) => `${counts.get(s)} ${s}`)
    .join(" · ");
}

/** A wall of status squares: 117 containers readable in one glance, trouble in colour. */
function Wall({ items }: { items: ListData["items"] }) {
  return (
    <ul className="wall">
      {items.map((item) => {
        const label = `${item.title}: ${item.state}`;
        return (
          <li key={item.uid} className="wall__cell" data-state={item.state} data-stale={item.stale || undefined}>
            {item.links["ui"] ? (
              <a href={item.links["ui"]} target="_blank" rel="noreferrer noopener" aria-label={label} title={label} />
            ) : (
              <span role="img" aria-label={label} title={label} />
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** A card per resource. With a value it is a reading (Living room · 21.5 °C); without,
 *  an app (icon, name, description) that opens the app when it has a UI link. */
function Cards({ items }: { items: ListData["items"] }) {
  return (
    <ul className="cards">
      {items.map((item) => {
        const { sub, values, state } = splitFields(item.fields.filter((f) => String(f.value) !== item.title));
        const reading = values[0];
        const url = item.links["ui"];
        if (reading) {
          const [number, unit] = valueParts(fieldText(reading), reading.unit);
          return (
            <li key={item.uid} className="card card--reading" data-state={item.state} data-stale={item.stale || undefined}>
              <span className="card__label">
                <span className="status-dot" data-state={item.state} aria-label={item.state} />
                <span className="card__title">{item.title}</span>
              </span>
              <span className="card__value">
                <span className="card__number">{number}</span>
                {unit ? <span className="metric__unit">{unit}</span> : null}
              </span>
              {state ? (
                <span className="list__state" data-state={state}>
                  {state}
                </span>
              ) : null}
            </li>
          );
        }
        const body = (
          <>
            <Icon name={item.icon} title={item.title} />
            <span className="card__text">
              <span className="card__title">{item.title}</span>
              {sub.length > 0 ? <span className="card__sub">{sub.map(fieldText).join(" · ")}</span> : null}
            </span>
            <span className="status-dot" data-state={item.state} aria-label={item.state} />
          </>
        );
        return (
          <li key={item.uid} className="card" data-state={item.state} data-stale={item.stale || undefined}>
            {url ? (
              <a className="card__link" href={url} target="_blank" rel="noreferrer noopener">
                {body}
              </a>
            ) : (
              <span className="card__link">{body}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function ListWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as ListData;
  if (data.layout === "cards" && data.items.length > 0) {
    return (
      <WidgetFrame widget={widget}>
        <Cards items={data.items} />
      </WidgetFrame>
    );
  }
  if (data.layout === "grid" && data.items.length > 0) {
    return (
      <WidgetFrame widget={widget}>
        <p className="wall__summary">{stateCounts(data.items)}</p>
        <Wall items={data.items} />
      </WidgetFrame>
    );
  }
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
                {item.image ? <Poster uid={item.uid} /> : null}
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
