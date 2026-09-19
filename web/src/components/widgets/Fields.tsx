// SPDX-License-Identifier: Apache-2.0
import { formatAge, formatValue } from "../../api/format";
import type { FieldValue } from "../../api/types";

/** Text for one resolved field; timestamps read as ages. */
export function fieldText(f: FieldValue): string {
  if (f.key === "fetched_at" && typeof f.value === "string") return formatAge(f.value);
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
