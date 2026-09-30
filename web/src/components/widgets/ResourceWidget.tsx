// SPDX-License-Identifier: Apache-2.0
import { formatAge, formatValue } from "../../api/format";
import type { HeroStat, ResolvedWidget, ResourceData } from "../../api/types";
import { useTweened } from "../../hooks/useTweened";
import { WidgetFrame } from "../WidgetFrame";
import { fieldText, Fields } from "./Fields";
import { Gauge } from "./Gauge";
import { valueParts } from "./MetricWidget";
import { MiniSpark } from "./MiniSpark";
import { Lead, ReadingValue } from "./Lead";
import { TrendArea, trendRange } from "./TrendArea";

/** One hero reading: a percentage as a small dial, anything else as a number. */
function Stat({ stat }: { stat: HeroStat }) {
  const value = useTweened(stat.value);
  const trend = stat.sparkline && stat.sparkline.length > 1 ? <MiniSpark points={stat.sparkline} title={stat.label} /> : null;
  if (stat.unit === "pct" && value !== null) {
    return (
      <div className="hero__stat hero__stat--gauge">
        <Gauge pct={value} state={stat.state}>
          <span className="hero__number">{value.toFixed(0)}</span>
          <span className="metric__unit">%</span>
        </Gauge>
        <span className="hero__label">{stat.label}</span>
        {trend}
      </div>
    );
  }
  const [number, unit] = valueParts(formatValue(value, stat.unit, 1), stat.unit);
  return (
    <div className="hero__stat" data-state={stat.state}>
      <span className="hero__value">
        <span className="hero__number">{number}</span>
        {unit ? <span className="metric__unit">{unit}</span> : null}
      </span>
      <span className="hero__label">{stat.label}</span>
      {trend}
    </div>
  );
}

/** A wide header: the resource's name large, its fields as a subtitle, and a row of
 *  readings from other resources (CPU, memory, uptime). */
function Hero({ data }: { data: ResourceData }) {
  const r = data.resource;
  const sub = data.fields.filter((f) => f.key !== "name" && f.value !== null && f.value !== "");
  return (
    <div className="hero">
      <div className="hero__id">
        <span className="hero__name">{r?.name ?? "—"}</span>
        {sub.length > 0 ? <span className="hero__sub">{sub.map(fieldText).join(" · ")}</span> : null}
      </div>
      <div className="hero__stats">
        {(data.stats ?? []).map((s) => (
          <Stat key={s.label} stat={s} />
        ))}
      </div>
    </div>
  );
}

function subtitle(data: ResourceData): string | null {
  const sub = data.fields.filter((f) => f.key !== "name" && f.value !== null && f.value !== "");
  return sub.length > 0 ? sub.map(fieldText).join(" · ") : null;
}

/** One reading as its own panel: the number large, its recent history behind it. */
function Reading({ stat }: { stat: HeroStat }) {
  const value = useTweened(stat.value);
  const fmt = (v: number) => formatValue(v, stat.unit, stat.unit === "pct" ? 0 : 1);
  const range = trendRange(stat.sparkline, fmt);
  return (
    <div className="reading" data-state={stat.state}>
      <span className="reading__label">{stat.label}</span>
      <ReadingValue stat={stat} value={value} />
      {range ? (
        <span className="reading__range">
          {stat.sparkline_range ? `last ${stat.sparkline_range}: ` : null}
          {range}
        </span>
      ) : null}
      {stat.sparkline ? <TrendArea points={stat.sparkline} placement="behind" title={stat.label} /> : null}
    </div>
  );
}

/** style: readings — each stat a panel, side by side. */
function Readings({ data }: { data: ResourceData }) {
  const sub = subtitle(data);
  return (
    <div className="readings-block">
      {sub ? <p className="readings-block__sub">{sub}</p> : null}
      <div className="readings">
        {(data.stats ?? []).map((s) => (
          <Reading key={s.label} stat={s} />
        ))}
      </div>
    </div>
  );
}

export function ResourceWidget({ widget }: { widget: ResolvedWidget }) {
  const data = widget.data as unknown as ResourceData;
  const r = data.resource;
  const body =
    data.style === "hero" ? (
      <Hero data={data} />
    ) : data.style === "readings" ? (
      <Readings data={data} />
    ) : data.style === "lead" ? (
      <Lead stats={data.stats ?? []} sub={subtitle(data)} />
    ) : (
      <Fields fields={data.fields} />
    );
  return (
    <WidgetFrame widget={widget} className={data.style === "hero" ? "widget--hero" : data.style ? `widget--${data.style}` : undefined}>
      {body}
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
