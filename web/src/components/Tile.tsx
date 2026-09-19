// SPDX-License-Identifier: Apache-2.0
import type { ReactNode } from "react";

export type TileState = "up" | "down" | "degraded" | "unknown" | "paused";

export interface TileProps {
  title: string;
  state: TileState;
  rows: ReadonlyArray<readonly [label: string, value: ReactNode]>;
  error?: string | undefined;
}

// A tile degrades loudly (invariant 6): an error is rendered inside the tile, in status
// color, alongside whatever last-known values it still has. Never blank.
export function Tile({ title, state, rows, error }: TileProps) {
  return (
    <section className="tile" aria-label={title}>
      <h2 className="tile__title">
        <span className="status-dot" data-state={state} aria-label={state} />
        {title}
      </h2>
      {rows.map(([label, value]) => (
        <TileRow key={label} label={label} value={value} />
      ))}
      {error ? (
        <p className="tile__error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function TileRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <>
      <dt className="tile__dt">{label}</dt>
      <dd className="tile__dd">{value}</dd>
    </>
  );
}
