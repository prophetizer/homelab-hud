// SPDX-License-Identifier: Apache-2.0
import { formatValue } from "../../api/format";
import type { HeroStat } from "../../api/types";
import { useTweened } from "../../hooks/useTweened";
import { valueParts } from "./MetricWidget";
import { TrendArea } from "./TrendArea";

/** A number as it is read: "36" and "%", "114" and "Mbit/s". */
export function ReadingValue({ stat, value }: { stat: HeroStat; value: number | null }) {
  const [number, unit] = valueParts(formatValue(value, stat.unit, stat.unit === "pct" ? 0 : 1), stat.unit);
  return (
    <span className="reading__value">
      <span className="reading__number">{number}</span>
      {unit ? <span className="reading__unit">{unit}</span> : null}
    </span>
  );
}

/** style: lead — the first stat is the tile's number, its trend beneath; the rest are facts. */
export function Lead({ stats, sub }: { stats: HeroStat[]; sub?: string | null }) {
  const [first, ...rest] = stats;
  const value = useTweened(first?.value ?? null);
  if (!first) return null;
  return (
    <div className="lead" data-state={first.state}>
      {sub ? <p className="readings-block__sub">{sub}</p> : null}
      <div className="lead__main">
        <ReadingValue stat={first} value={value} />
        <span className="lead__label">{first.label}</span>
      </div>
      {rest.length > 0 ? (
        <p className="lead__facts">
          {rest.map((s) => (
            <span key={s.label} data-state={s.state}>
              <b>{formatValue(s.value, s.unit, s.unit === "pct" ? 0 : 1)}</b> {s.label}
            </span>
          ))}
        </p>
      ) : null}
      {first.sparkline ? <TrendArea points={first.sparkline} placement="flow" title={first.label} /> : null}
    </div>
  );
}
