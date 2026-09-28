// SPDX-License-Identifier: Apache-2.0
import { type FormEvent, useEffect, useState } from "react";
import type { Me } from "../api/auth";
import { formatAge, formatValue } from "../api/format";
import {
  type Header,
  type HeaderWeather,
  firstName,
  greeting,
  hourIn,
  searchHref,
  temperature,
  windSpeed,
} from "../api/header";
import type { HeroStat } from "../api/types";
import { Icon } from "./widgets/Icon";
import { Meter } from "./widgets/Meter";

/** Ticks once a second, aligned to the second, for the clock. */
function useNow(enabled: boolean): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (!enabled) return;
    let timer = 0;
    const tick = () => {
      setNow(new Date());
      timer = window.setTimeout(tick, 1000 - (Date.now() % 1000));
    };
    timer = window.setTimeout(tick, 1000 - (Date.now() % 1000));
    return () => window.clearTimeout(timer);
  }, [enabled]);
  return now;
}

function format(now: Date, timeZone: string, opts: Intl.DateTimeFormatOptions): string {
  try {
    return new Intl.DateTimeFormat(undefined, { ...opts, timeZone }).format(now);
  } catch {
    return new Intl.DateTimeFormat(undefined, opts).format(now);
  }
}

/** The strip above every board: who and when, a web search, the weather, a few readings.
 *  Everything in it is named in settings.yaml (header:), the same for everyone. */
export function HeaderBar({ header, me }: { header: Header; me: Me }) {
  const now = useNow(header.clock || header.greeting);
  const tz = header.timezone;
  const name = firstName(me.display_name);
  const hasRight = header.search_url || header.weather || header.stats.length > 0;
  return (
    <section className="hud-header" aria-label="Header">
      {header.greeting || header.clock ? (
        <div className="hud-header__hello">
          {header.greeting ? (
            <p className="hud-header__greeting">
              {greeting(hourIn(tz, now))}
              {name ? `, ${name}` : ""}
            </p>
          ) : null}
          {header.clock ? (
            <p className="hud-header__when">
              <time className="hud-header__time" dateTime={now.toISOString()}>
                {format(now, tz, { hour: "2-digit", minute: "2-digit" })}
              </time>
              <span className="hud-header__date">
                {format(now, tz, { weekday: "long", day: "numeric", month: "long" })}
              </span>
            </p>
          ) : null}
        </div>
      ) : null}
      {hasRight ? (
        <div className="hud-header__tools">
          {header.search_url ? <Search template={header.search_url} /> : null}
          {header.weather ? <WeatherChip weather={header.weather} /> : null}
          {header.stats.length > 0 ? (
            <ul className="hud-header__stats" aria-label="Readings">
              {header.stats.map((s) => (
                <HeaderStat key={s.label} stat={s} />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** The query goes from this browser straight to the engine; HUD never sees it. */
function Search({ template }: { template: string }) {
  const [q, setQ] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const href = q.trim() ? searchHref(template, q) : null;
    if (!href) return;
    window.open(href, "_blank", "noopener,noreferrer");
    setQ("");
  };
  return (
    <form className="hud-header__search" role="search" onSubmit={submit}>
      <Icon name="mdi-magnify" title="Search" size="sm" fallback="none" />
      <input
        className="hud-header__search-input"
        type="search"
        placeholder="Search the web"
        aria-label="Search the web"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        autoComplete="off"
        enterKeyHint="search"
      />
    </form>
  );
}

function WeatherChip({ weather }: { weather: HeaderWeather }) {
  const a = weather.attrs;
  const units = a.units;
  // Nothing to show yet: failed says so, with when it last worked (invariant 6); a first
  // poll still running is only pending.
  if (a.temperature === undefined) {
    const failed = weather.error !== null;
    return (
      <div className="hud-header__weather hud-header__weather--failed" title={weather.error ?? undefined}>
        <span className="status-dot" data-state={failed ? "down" : "unknown"} aria-hidden="true" />
        <span>{failed ? "Weather unavailable" : "Weather…"}</span>
        {failed && weather.last_success ? (
          <span className="hud-header__faint">· last {formatAge(weather.last_success)}</span>
        ) : null}
      </div>
    );
  }
  const stale = weather.stale || weather.error !== null;
  const wind = windSpeed(a.wind_kmh, units);
  return (
    <details className="hud-header__weather" data-stale={stale || undefined}>
      <summary className="hud-header__weather-summary" title={weather.error ?? a.condition}>
        <Icon name={a.icon} title={a.condition ?? "Weather"} fallback="none" />
        <span className="hud-header__temp">{temperature(a.temperature, units)}</span>
        <span className="hud-header__weather-text">
          <span>{a.condition}</span>
          <span className="hud-header__faint">
            H {temperature(a.high, units)} · L {temperature(a.low, units)}
            {stale ? ` · stale, ${formatAge(weather.last_success)}` : ""}
          </span>
        </span>
      </summary>
      <div className="hud-header__forecast">
        <dl className="hud-header__facts">
          {weather.metrics.feels_like !== undefined ? (
            <>
              <dt>Feels like</dt>
              <dd>{temperature(weather.metrics.feels_like, units)}</dd>
            </>
          ) : null}
          {weather.metrics.humidity !== undefined ? (
            <>
              <dt>Humidity</dt>
              <dd>{Math.round(weather.metrics.humidity)}%</dd>
            </>
          ) : null}
          {wind ? (
            <>
              <dt>Wind</dt>
              <dd>{wind}</dd>
            </>
          ) : null}
          {a.sunrise && a.sunset ? (
            <>
              <dt>Sun</dt>
              <dd>
                {clockOf(a.sunrise)} – {clockOf(a.sunset)}
              </dd>
            </>
          ) : null}
        </dl>
        {a.forecast && a.forecast.length > 1 ? (
          <ol className="hud-header__days">
            {a.forecast.map((d, i) => (
              <li key={d.date} className="hud-header__day">
                <span className="hud-header__faint">{i === 0 ? "Today" : weekday(d.date)}</span>
                <Icon name={d.icon} title={d.condition} size="sm" fallback="none" />
                <span className="hud-header__day-temps">
                  {temperature(d.high, units)} <span className="hud-header__faint">{temperature(d.low, units)}</span>
                </span>
                {d.precipitation_chance ? (
                  <span className="hud-header__faint">{d.precipitation_chance}%</span>
                ) : (
                  <span />
                )}
              </li>
            ))}
          </ol>
        ) : null}
        {weather.error ? <p className="hud-header__error">{weather.error}</p> : null}
      </div>
    </details>
  );
}

function HeaderStat({ stat }: { stat: HeroStat }) {
  return (
    <li className="hud-header__stat" data-state={stat.state}>
      <span className="hud-header__stat-label">{stat.label}</span>
      <span className="hud-header__stat-value">{formatValue(stat.value, stat.unit, stat.unit === "pct" ? 0 : 1)}</span>
      {stat.unit === "pct" && stat.value !== null ? <Meter pct={stat.value} state={stat.state} /> : null}
    </li>
  );
}

/** "2026-09-27T06:44" (already the location's local time) → "06:44". */
function clockOf(iso: string): string {
  return iso.split("T")[1]?.slice(0, 5) ?? iso;
}

function weekday(date: string): string {
  const d = new Date(`${date}T12:00:00Z`);
  return Number.isNaN(d.getTime())
    ? date
    : new Intl.DateTimeFormat(undefined, { weekday: "short", timeZone: "UTC" }).format(d);
}
