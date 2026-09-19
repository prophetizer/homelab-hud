// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import { fetchHealth, formatBytes, formatUptime, type Health } from "./api/health";
import { Tile, type TileState } from "./components/Tile";

const POLL_MS = 15_000;

interface HealthView {
  health: Health | null;
  error: string | null;
  fetchedAt: Date | null;
}

export function App() {
  const [view, setView] = useState<HealthView>({ health: null, error: null, fetchedAt: null });

  useEffect(() => {
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      try {
        const health = await fetchHealth(ctrl.signal);
        setView({ health, error: null, fetchedAt: new Date() });
      } catch (err) {
        if (ctrl.signal.aborted) return;
        // Keep the last good payload; surface the error next to it.
        setView((prev) => ({ ...prev, error: err instanceof Error ? err.message : String(err) }));
      }
      if (!ctrl.signal.aborted) timer = setTimeout(tick, POLL_MS);
    };
    void tick();

    return () => {
      ctrl.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, []);

  const { health, error, fetchedAt } = view;
  const state: TileState = error ? "down" : health?.status === "degraded" ? "degraded" : health ? "up" : "unknown";

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar__brand">HUD</div>
        <nav className="sidebar__nav" aria-label="Boards">
          <a href="/" aria-current="page">
            Overview
          </a>
        </nav>
      </aside>
      <main className="main">
        <div className="board">
          <Tile
            title="HUD"
            state={state}
            rows={[
              ["Version", health?.app_version ?? "—"],
              ["Uptime", health ? formatUptime(health.uptime_seconds) : "—"],
              ["Config", health?.config.version ?? "—"],
              ["Database", health ? formatBytes(health.db.size_bytes) : "—"],
              ["Fetched", fetchedAt ? fetchedAt.toLocaleTimeString() : "—"],
            ]}
            error={error ?? health?.config.error ?? undefined}
          />
        </div>
      </main>
      <footer className="footer">
        <span>config {health?.config.version ?? "—"}</span>
        <span>v{health?.app_version ?? "—"}</span>
        {health && health.config.warnings > 0 ? <span>{health.config.warnings} config warning(s)</span> : null}
      </footer>
    </div>
  );
}
