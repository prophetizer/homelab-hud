// SPDX-License-Identifier: Apache-2.0
import { getJson } from "./client";
import type { HeroStat, State } from "./types";

export interface ForecastDay {
  date: string;
  condition: string;
  icon: string;
  high: number | null;
  low: number | null;
  precipitation_chance: number | null;
  sunrise: string | null;
  sunset: string | null;
}

export interface HeaderWeather {
  uid: string;
  name: string | null;
  state: State;
  stale: boolean;
  error: string | null;
  last_success: string | null;
  attrs: {
    condition?: string;
    icon?: string;
    is_day?: boolean;
    units?: "metric" | "imperial";
    temperature?: number | null;
    high?: number | null;
    low?: number | null;
    wind_kmh?: number | null;
    sunrise?: string | null;
    sunset?: string | null;
    forecast?: ForecastDay[];
  };
  metrics: { feels_like?: number; humidity?: number; precipitation_chance?: number };
}

export interface HeaderAppearance {
  background: string | null;
  dim: number;
  blur: number;
  translucent: boolean;
}

export interface Header {
  enabled: boolean;
  title: string;
  timezone: string;
  greeting: boolean;
  clock: boolean;
  search_url: string | null;
  weather: HeaderWeather | null;
  stats: HeroStat[];
  appearance: HeaderAppearance;
}

export function fetchHeader(signal?: AbortSignal): Promise<Header> {
  return getJson("/api/v1/header", signal);
}

/** The hour (0-23) in a timezone; the browser's own when the zone is unknown. */
export function hourIn(timeZone: string, now: Date): number {
  try {
    const h = new Intl.DateTimeFormat("en-GB", { hour: "numeric", hourCycle: "h23", timeZone }).format(now);
    return Number.parseInt(h, 10) % 24;
  } catch {
    return now.getHours();
  }
}

export function greeting(hour: number): string {
  if (hour < 5) return "Good night";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  if (hour < 22) return "Good evening";
  return "Good night";
}

/** A person's first name from a display name ("Ada Lovelace" → "Ada"). */
export function firstName(displayName: string): string {
  return displayName.trim().split(/\s+/)[0] ?? "";
}

/** The engine's URL with the query in place of {q}; only http(s) templates are used. */
export function searchHref(template: string, query: string): string | null {
  if (!/^https?:\/\//.test(template) || !template.includes("{q}")) return null;
  return template.replaceAll("{q}", encodeURIComponent(query.trim()));
}

/** A Celsius reading in the unit the weather provider asks to be shown in. */
export function temperature(celsius: number | null | undefined, units: "metric" | "imperial" | undefined): string {
  if (celsius === null || celsius === undefined || !Number.isFinite(celsius)) return "—";
  const v = units === "imperial" ? (celsius * 9) / 5 + 32 : celsius;
  return `${Math.round(v)}°`;
}

export function windSpeed(kmh: number | null | undefined, units: "metric" | "imperial" | undefined): string | null {
  if (kmh === null || kmh === undefined || !Number.isFinite(kmh)) return null;
  return units === "imperial" ? `${Math.round(kmh / 1.609344)} mph` : `${Math.round(kmh)} km/h`;
}
