// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import { formatValue } from "../../api/format";
import { clock, livePosition, reasonWords } from "../../api/playback";
import type { FieldValue, ListData, ResolvedWidget, State } from "../../api/types";
import { WidgetFrame } from "../WidgetFrame";
import { Empty } from "./Empty";
import { Icon } from "./Icon";
import { Meter } from "./Meter";
import { MiniSpark } from "./MiniSpark";
import { Poster } from "./Poster";
import { valueParts } from "./MetricWidget";
import { cellState, formatSla } from "./UptimeWidget";
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

/** A row's last 24 h as a thin strip of hourly cells, with the day's %. */
function MiniUptime({ uptime }: { uptime: NonNullable<ListData["items"][number]["uptime"]> }) {
  return (
    <span className="mini-uptime" title={uptime.sla === null ? "not observed in 24 h" : `${formatSla(uptime.sla)} over 24 h`}>
      <span className="mini-uptime__strip">
        {uptime.cells.map((c) => (
          <span key={c.t} className="uptime__cell" data-state={cellState(c)} />
        ))}
      </span>
      {uptime.sla !== null ? <span className="mini-uptime__sla">{formatSla(uptime.sla)}</span> : null}
    </span>
  );
}

/** A wall of status squares: 117 containers readable in one glance, trouble in colour. The
 *  square under the pointer (or keyboard focus) is described in a line beneath the wall —
 *  a floating card would be clipped at the tile's edge. */
function Wall({ items }: { items: ListData["items"] }) {
  const [focus, setFocus] = useState<string | null>(null);
  const item = items.find((i) => i.uid === focus);
  return (
    <>
      <ul className="wall" onPointerLeave={() => setFocus(null)}>
        {items.map((it) => {
          const label = `${it.title}: ${it.state}`;
          const on = { onPointerEnter: () => setFocus(it.uid), onFocus: () => setFocus(it.uid) };
          return (
            <li key={it.uid} className="wall__cell" data-state={it.state} data-stale={it.stale || undefined} data-focus={focus === it.uid || undefined}>
              {it.links["ui"] ? (
                <a href={it.links["ui"]} target="_blank" rel="noreferrer noopener" aria-label={label} {...on} />
              ) : (
                <span role="img" aria-label={label} tabIndex={0} {...on} />
              )}
            </li>
          );
        })}
      </ul>
      <div className="wall__inspector" aria-live="polite">
        {item ? (
          <>
            <span className="status-dot" data-state={item.state} />
            <span className="wall__name">{item.title}</span>
            <span className="wall__detail">
              {[item.state, ...item.fields.filter((f) => f.key !== "name" && f.key !== "state" && f.value !== null && f.value !== "").map(fieldText)].join(" · ")}
            </span>
            {item.uptime ? <MiniUptime uptime={item.uptime} /> : null}
          </>
        ) : (
          <span className="wall__hint">Point at a square for details</span>
        )}
      </div>
    </>
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
              {item.uptime ? <MiniUptime uptime={item.uptime} /> : null}
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

/** Now playing as cards: the poster first, then what, who and where, and how far in. */
// Playback fields the media card draws itself (progress clock, badges, reason) rather than
// listing them as text.
const PLAYBACK_KEYS = new Set([
  "metric.position_seconds",
  "metric.duration_seconds",
  "attrs.playback",
  "attrs.transcode_reason",
  "attrs.resolution",
]);

/** The time, once a second, while ``active``: a playing stream's clock moves on. */
function useTick(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** A card per stream: the show's backdrop behind, its poster, who and where, a progress bar
 *  that moves on between polls while playing, and how it plays (direct or transcoded, and
 *  why). One stream on its own gets the whole width. */
function MediaCards({ items }: { items: ListData["items"] }) {
  const live = items.some((i) => i.fields.some((f) => f.key === "metric.position_seconds"));
  const now = useTick(live);
  return (
    <ul className={items.length === 1 ? "media-cards media-cards--single" : "media-cards"}>
      {items.map((item) => {
        const field = (key: string) => item.fields.find((f) => f.key === key)?.value;
        const { sub, values, state } = splitFields(
          item.fields.filter((f) => String(f.value) !== item.title && !PLAYBACK_KEYS.has(f.key)),
        );
        const position = num(field("metric.position_seconds"));
        const duration = num(field("metric.duration_seconds"));
        const playing = item.state !== "paused";
        const at = position !== null ? livePosition(position, duration, item.at, now, playing) : null;
        const pct = at !== null && duration ? (at / duration) * 100 : (item.bar ?? null);
        const playback = field("attrs.playback");
        const resolution = field("attrs.resolution");
        const reason = field("attrs.transcode_reason");
        return (
          <li key={item.uid} className="media-card" data-state={item.state} data-stale={item.stale || undefined}>
            {item.backdrop ? <Poster uid={item.uid} variant="backdrop" className="media-card__backdrop" /> : null}
            {item.image ? <Poster uid={item.uid} large /> : null}
            <div className="media-card__body">
              <span className="media-card__title">
                <span className="status-dot" data-state={item.state} aria-label={item.state} />
                {item.links["ui"] ? (
                  <a href={item.links["ui"]} target="_blank" rel="noreferrer noopener">
                    {item.title}
                  </a>
                ) : (
                  item.title
                )}
              </span>
              {sub.length > 0 ? <span className="media-card__sub">{sub.map(fieldText).join(" · ")}</span> : null}
              {playback || resolution ? (
                <span className="media-card__badges">
                  {typeof playback === "string" ? (
                    <span className="media-badge" data-kind={playback === "Transcode" ? "transcode" : "direct"}>
                      {playback}
                    </span>
                  ) : null}
                  {typeof resolution === "string" ? <span className="media-badge">{resolution}</span> : null}
                  {typeof reason === "string" && reason ? (
                    <span className="media-card__reason" title={reason}>
                      {reasonWords(reason)}
                    </span>
                  ) : null}
                </span>
              ) : null}
              {pct !== null ? (
                <span className="media-card__progress">
                  <Meter pct={pct} state={item.state} label={`${pct.toFixed(0)} % played`} />
                  {at !== null && duration ? (
                    <span className="media-card__clock">
                      {clock(at)} / {clock(duration)}
                      {!playing ? " · paused" : ""}
                    </span>
                  ) : null}
                </span>
              ) : null}
              <span className="media-card__meta">
                {state ? (
                  <span className="list__state" data-state={state}>
                    {state}
                  </span>
                ) : null}
                {values.map((f) => (
                  <span key={f.key} title={f.label}>
                    {fieldText(f)}
                  </span>
                ))}
              </span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** A shelf: the latest posters in a row that scrolls sideways, newest first. */
function Shelf({ items }: { items: ListData["items"] }) {
  return (
    <ul className="shelf">
      {items.map((item) => {
        // Numbers too: Plex's addedAt is epoch seconds, shown as an age.
        const { sub, values } = splitFields(item.fields.filter((f) => String(f.value) !== item.title));
        const detail = [...sub, ...values];
        const many = (item.group_count ?? 1) > 1 ? `${item.group_count} new` : null;
        const body = (
          <>
            <span className="shelf__art">
              {item.image ? <Poster uid={item.uid} className="shelf__poster" /> : <span className="shelf__blank">{item.title.charAt(0)}</span>}
              {/* Quiet when fine: only a row that is not up carries a dot over its poster. */}
              {item.state !== "up" ? (
                <span className="status-dot shelf__state" data-state={item.state} aria-label={item.state} />
              ) : null}
            </span>
            <span className="shelf__title" title={item.title}>
              {item.title}
            </span>
            {detail.length > 0 || many ? (
              <span className="shelf__sub">{[many, ...detail.map(fieldText)].filter(Boolean).join(" · ")}</span>
            ) : null}
          </>
        );
        return (
          <li key={item.uid} className="shelf__item">
            {item.links["ui"] ? (
              <a href={item.links["ui"]} target="_blank" rel="noreferrer noopener">
                {body}
              </a>
            ) : (
              body
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** An agenda heading: Today / Tomorrow / Yesterday, else "Tue 29 Sep". Days are plain
 *  dates (YYYY-MM-DD) already in settings.timezone, so no zone is applied here. */
export function dayLabel(day: string, today: string | undefined): string {
  const at = (d: string) => Date.parse(`${d}T12:00:00Z`);
  const t = today ? at(today) : Number.NaN;
  const d = at(day);
  if (Number.isNaN(d)) return day;
  const diff = Number.isNaN(t) ? Number.NaN : Math.round((d - t) / 86_400_000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff === -1) return "Yesterday";
  return new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }).format(d);
}

/** Rows under day headings, in the order the source sorted them: a release calendar. */
function Agenda({ items, today }: { items: ListData["items"]; today: string | undefined }) {
  const days: { day: string; items: ListData["items"] }[] = [];
  for (const item of items) {
    const day = item.when?.day ?? "";
    const last = days[days.length - 1];
    if (last && last.day === day) last.items.push(item);
    else days.push({ day, items: [item] });
  }
  return (
    <div className="agenda">
      {days.map((d) => (
        <section key={d.day || "undated"} className="agenda__day" data-today={d.day === today || undefined}>
          <h3 className="agenda__heading">{d.day ? dayLabel(d.day, today) : "Undated"}</h3>
          <ul className="agenda__items">
            {d.items.map((item) => {
              const { sub } = splitFields(item.fields.filter((f) => String(f.value) !== item.title));
              return (
                <li key={item.uid} className="agenda__item" data-stale={item.stale || undefined}>
                  <span className="status-dot" data-state={item.state} aria-label={item.state} />
                  {item.image ? <Poster uid={item.uid} className="agenda__poster" /> : null}
                  <span className="agenda__main">
                    <span className="agenda__title">{item.title}</span>
                    {sub.length > 0 ? <span className="agenda__sub">{sub.map(fieldText).join(" · ")}</span> : null}
                  </span>
                  {item.when?.time ? <time className="agenda__time">{item.when.time}</time> : null}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

/** display.stats: a line of readings above the rows — a queue's speed and time left. */
function StatsStrip({ stats }: { stats: NonNullable<ListData["stats"]> }) {
  return (
    <ul className="list-stats" aria-label="Readings">
      {stats.map((s) => (
        <li key={s.label} className="list-stats__item" data-state={s.state}>
          <span className="list-stats__label">{s.label}</span>
          <span className="list-stats__value">{formatValue(s.value, s.unit, s.unit === "pct" ? 0 : 1)}</span>
          {s.sparkline && s.sparkline.length > 1 ? <MiniSpark points={s.sparkline} title={s.label} /> : null}
        </li>
      ))}
    </ul>
  );
}

const WEEK_DAYS = 7;

/** The seven days from ``today`` as YYYY-MM-DD, computed on plain dates (no zone). */
export function weekDays(today: string): string[] {
  const t0 = Date.parse(`${today}T12:00:00Z`);
  if (Number.isNaN(t0)) return [];
  return Array.from({ length: WEEK_DAYS }, (_, i) => new Date(t0 + i * 86_400_000).toISOString().slice(0, 10));
}

/** Seven columns, today first; each day's rows as posters with the title and time. */
function Week({ items, today }: { items: ListData["items"]; today: string | undefined }) {
  const days = weekDays(today ?? new Date().toISOString().slice(0, 10));
  const byDay = new Map<string, ListData["items"]>(days.map((d) => [d, []]));
  let later = 0;
  for (const item of items) {
    const day = item.when?.day;
    const bucket = day ? byDay.get(day) : undefined;
    if (bucket) bucket.push(item);
    else if (day && day > (days[days.length - 1] ?? "")) later += 1;
  }
  return (
    <div className="week">
      <ol className="week__grid">
        {days.map((d) => (
          <li key={d} className="week__day" data-today={d === today || undefined}>
            <h3 className="week__heading">{dayLabel(d, today)}</h3>
            <ul className="week__items">
              {(byDay.get(d) ?? []).map((item) => {
                const { sub } = splitFields(item.fields.filter((f) => String(f.value) !== item.title));
                const label = [item.title, ...sub.map(fieldText), item.when?.time].filter(Boolean).join(" · ");
                return (
                  <li key={item.uid} className="week__item" title={label} data-stale={item.stale || undefined}>
                    <span className="week__art">
                      {item.image ? (
                        <Poster uid={item.uid} className="week__poster" />
                      ) : (
                        <span className="shelf__blank">{item.title.charAt(0)}</span>
                      )}
                      {/* Not yet out is the default and stays quiet; downloaded or missing shows. */}
                      {item.state === "unknown" ? null : (
                        <span className="status-dot week__state" data-state={item.state} aria-label={item.state} />
                      )}
                    </span>
                    <span className="week__title">{item.title}</span>
                    {item.when?.time ? <time className="week__time">{item.when.time}</time> : null}
                  </li>
                );
              })}
            </ul>
          </li>
        ))}
      </ol>
      {later > 0 ? <p className="week__more">+{later} later</p> : null}
    </div>
  );
}

/** display.dense: one line per row — dot, poster, name and detail, bar, values. */
function DenseRows({ items }: { items: ListData["items"] }) {
  // Every row keeps the same cells, so names and bars line up whether or not it has art.
  const posters = items.some((i) => i.image);
  return (
    <ul className="list list--dense">
      {items.map((item) => {
        const { sub, values, state } = splitFields(item.fields.filter((f) => String(f.value) !== item.title));
        return (
          <li key={item.uid} className="dense-row" data-posters={posters || undefined} data-stale={item.stale || undefined}>
            <span className="status-dot" data-state={item.state} aria-label={item.state} />
            {item.image ? (
              <Poster uid={item.uid} className="dense-row__poster" />
            ) : (
              <span className="dense-row__poster dense-row__poster--none" aria-hidden="true" />
            )}
            <span className="dense-row__name" title={[item.title, ...sub.map(fieldText)].join(" · ")}>
              {item.title}
              {sub.length > 0 ? <span className="dense-row__sub"> · {sub.map(fieldText).join(" · ")}</span> : null}
            </span>
            {item.bar !== undefined && item.bar !== null ? (
              <span className="dense-row__bar">
                <Meter pct={item.bar} state={item.state} label={`${item.bar.toFixed(0)} %`} />
                <span className="dense-row__pct">{item.bar.toFixed(0)}%</span>
              </span>
            ) : (
              <span />
            )}
            <span className="dense-row__values">
              {state ? (
                <span className="list__state" data-state={state}>
                  {state}
                </span>
              ) : null}
              {values.map((v) => (
                <span key={v.key} className="dense-row__value">
                  {fieldText(v)}
                </span>
              ))}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** The ring's arc for a 24 h uptime: closed as far as the share of time up. With no
 *  uptime data the ring is whole (it still carries the state's colour). */
export function ringDash(sla: number | null | undefined, circumference: number): string {
  if (sla === null || sla === undefined) return `${circumference} 0`;
  const on = (Math.max(0, Math.min(100, sla)) / 100) * circumference;
  return `${on} ${circumference - on}`;
}

/** A tile's uptime, short enough for a narrow tile: "100 %", "99.9 %", "97 %". */
export function tileSla(pct: number): string {
  if (pct >= 99.95) return pct >= 99.995 ? "100 %" : `${pct.toFixed(2)} %`;
  if (pct >= 99) return `${pct.toFixed(1)} %`;
  return `${Math.floor(pct)} %`;
}

const RING_R = 19;
const RING_C = 2 * Math.PI * RING_R;

/** layout: services — an app tile per row: its icon inside a ring that is its state, the
 *  ring closed as far as its 24 h uptime; the first reading (a response time) and trend. */
function ServiceCards({ items }: { items: ListData["items"] }) {
  return (
    <ul className="svc">
      {items.map((item) => {
        const { values, state } = splitFields(item.fields.filter((f) => String(f.value) !== item.title));
        const reading = values[0];
        const sla = item.uptime?.sla ?? null;
        const url = item.links["ui"];
        const body = (
          <>
            <span className="svc__badge">
              <svg className="svc__ring" viewBox="0 0 44 44" aria-hidden="true">
                <circle className="svc__track" data-state={item.state} cx="22" cy="22" r={RING_R} />
                <circle
                  className="svc__arc"
                  data-state={item.state}
                  cx="22"
                  cy="22"
                  r={RING_R}
                  strokeDasharray={ringDash(sla, RING_C)}
                  transform="rotate(-90 22 22)"
                />
              </svg>
              <Icon name={item.icon} title={item.title} />
            </span>
            <span className="svc__name" title={item.title}>
              {item.title}
            </span>
            <span className="svc__meta">
              {state ? (
                <span className="list__state" data-state={state}>
                  {state}
                </span>
              ) : reading ? (
                <span className="svc__reading">{fieldText(reading)}</span>
              ) : null}
              {sla !== null ? (
                <span className="svc__sla" title={`${formatSla(sla)} over 24 h`}>
                  {tileSla(sla)}
                </span>
              ) : null}
            </span>
            {item.trend && item.trend.length > 1 ? (
              <span className="svc__trend">
                <MiniSpark points={item.trend} title={`${item.title}: last 6 h`} />
              </span>
            ) : null}
          </>
        );
        return (
          <li key={item.uid} className="svc__item" data-state={item.state} data-stale={item.stale || undefined}>
            {url ? (
              <a className="svc__link" href={url} target="_blank" rel="noreferrer noopener" aria-label={`${item.title}, ${item.state}`}>
                {body}
              </a>
            ) : (
              <span className="svc__link">{body}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function ListWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as ListData;
  const stats = data.stats && data.stats.length > 0 ? <StatsStrip stats={data.stats} /> : null;
  if (data.layout === "services" && data.items.length > 0) {
    return (
      <WidgetFrame widget={widget}>
        {stats}
        <ServiceCards items={data.items} />
      </WidgetFrame>
    );
  }
  if (data.layout === "week") {
    return (
      <WidgetFrame widget={widget}>
        {stats}
        <Week items={data.items} today={data.today} />
      </WidgetFrame>
    );
  }
  if (data.dense && data.items.length > 0 && (!data.layout || data.layout === "rows")) {
    return (
      <WidgetFrame widget={widget}>
        {stats}
        <DenseRows items={data.items} />
      </WidgetFrame>
    );
  }
  if (data.layout === "agenda" && data.items.length > 0) {
    return (
      <WidgetFrame widget={widget}>
        {stats}
        <Agenda items={data.items} today={data.today} />
      </WidgetFrame>
    );
  }
  if (data.layout === "shelf" && data.items.length > 0) {
    return (
      <WidgetFrame widget={widget}>
        {stats}
        <Shelf items={data.items} />
      </WidgetFrame>
    );
  }
  if (data.layout === "media" && data.items.length > 0) {
    return (
      <WidgetFrame widget={widget}>
        {stats}
        <MediaCards items={data.items} />
      </WidgetFrame>
    );
  }
  if (data.layout === "cards" && data.items.length > 0) {
    return (
      <WidgetFrame widget={widget}>
        {stats}
        <Cards items={data.items} />
      </WidgetFrame>
    );
  }
  if (data.layout === "grid" && data.items.length > 0) {
    return (
      <WidgetFrame widget={widget}>
        {stats}
        <p className="wall__summary">{stateCounts(data.items)}</p>
        <Wall items={data.items} />
      </WidgetFrame>
    );
  }
  return (
    <WidgetFrame widget={widget}>
      {stats}
      {data.items.length === 0 ? (
        <Empty icon={widget.icon} text={data.empty_text} />
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
                {item.trend && item.trend.length > 1 ? <MiniSpark points={item.trend} title={`${item.title}: last 6 h`} /> : null}
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
