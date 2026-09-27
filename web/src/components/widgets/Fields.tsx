// SPDX-License-Identifier: Apache-2.0
import { formatAge, formatValue } from "../../api/format";
import type { FieldValue } from "../../api/types";

/** Text for one resolved field; timestamps read as ages. */
/** A timestamp field (``*_at``): Plex's epoch seconds or an ISO string, as an age. */
function asAge(key: string, value: unknown): string | null {
  if (!key.endsWith("_at")) return null;
  if (typeof value === "number" || (typeof value === "string" && /^\d{9,11}$/.test(value))) {
    return formatAge(new Date(Number(value) * 1000).toISOString());
  }
  if (typeof value === "string" && !Number.isNaN(Date.parse(value))) return formatAge(value);
  return null;
}

export function fieldText(f: FieldValue): string {
  if (f.key === "fetched_at" && typeof f.value === "string") return formatAge(f.value);
  const age = asAge(f.key, f.value);
  if (age !== null) return age;
  return formatValue(f.value, f.unit);
}

export function Fields({ fields }: { fields: FieldValue[] }) {
  if (fields.length === 0) return null;
  return (
    <dl className="fields">
      {fields.map((f) => (
        <div key={f.key} className="fields__row">
          <dt className="fields__dt">{f.label}</dt>
          <dd className="fields__dd" data-state={f.key === "state" ? String(f.value) : undefined}>
            {fieldText(f)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
