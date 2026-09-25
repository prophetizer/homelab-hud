// SPDX-License-Identifier: Apache-2.0
import { formatBytes, formatUptime, type Health } from "../api/health";
import { formatAge } from "../api/format";
import type { ProviderHealth } from "../api/types";
import { Tile, type TileState } from "./Tile";

interface Props {
  health: Health | null;
  error: string | null;
  fetchedAt: Date | null;
}

const PROVIDER_STATE: Record<ProviderHealth["status"], TileState> = {
  ok: "up",
  degraded: "degraded",
  error: "down",
  starting: "unknown",
  stopped: "paused",
};

// The overview is HUD looking at itself: process health plus one tile per provider.
export function Overview({ health, error, fetchedAt }: Props) {
  const state: TileState = error ? "down" : health?.status === "degraded" ? "degraded" : health ? "up" : "unknown";
  return (
    <div className="board">
      <Tile
        title="HUD"
        state={state}
        rows={[
          ["Version", health?.app_version ?? "—"],
          ["Uptime", health?.uptime_seconds !== undefined ? formatUptime(health.uptime_seconds) : "—"],
          ["Config", health?.config?.version ?? "—"],
          ["Database", health?.db ? formatBytes(health.db.size_bytes) : "—"],
          ["Providers", health?.providers ? String(health.providers.length) : "—"],
          ["Fetched", fetchedAt ? fetchedAt.toLocaleTimeString() : "—"],
        ]}
        error={error ?? health?.config?.error ?? undefined}
      />
      {health?.config?.quarantined.map((q) => (
        // One tile per invalid file (invariant 6): a board set aside this way reaches no
        // user's sidebar, so this is where an operator finds out.
        <Tile
          key={q.file}
          title={q.name ?? q.file}
          state={q.serving_last_good ? "degraded" : "down"}
          rows={[
            ["File", q.file],
            ["Kind", q.kind],
            ["In use", q.serving_last_good ? "last valid version" : "not loaded"],
          ]}
          error={q.issues.join("\n")}
        />
      ))}
      {health?.providers?.map((p) => (
        <Tile
          key={p.name}
          title={p.name}
          state={PROVIDER_STATE[p.status]}
          rows={[
            ["Tier", p.tier],
            ["Status", p.status],
            ["Resources", String(p.resource_count)],
            ["Last poll", formatAge(p.last_poll)],
            ["Last success", formatAge(p.last_success)],
            ...(p.circuit_open_until ? [["Circuit open until", new Date(p.circuit_open_until).toLocaleTimeString()] as const] : []),
          ]}
          error={p.last_error ?? undefined}
        />
      ))}
      {health?.providers && health.providers.length === 0 ? (
        <p className="board-status">No providers configured. Add one under /config/providers/.</p>
      ) : null}
      {health && !health.providers ? (
        <p className="board-status">Provider health needs the <code>providers:view</code> permission.</p>
      ) : null}
    </div>
  );
}
