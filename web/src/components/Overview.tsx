// SPDX-License-Identifier: Apache-2.0
import { formatBytes, formatUptime, type Health } from "../api/health";
import { formatAge } from "../api/format";
import type { ProviderHealth } from "../api/types";
import type { TileState } from "./Tile";

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

const ORDER: Record<ProviderHealth["status"], number> = {
  error: 0,
  degraded: 1,
  starting: 2,
  stopped: 3,
  ok: 4,
};

// The System page: HUD looking at itself. An operator's view — dense, failing first, and
// quiet about everything that is fine. (It used to be the landing page: ten tall tiles
// reading "Tier: declarative / Status: ok".)
export function Overview({ health, error, fetchedAt }: Props) {
  const providers = [...(health?.providers ?? [])].sort(
    (a, b) => ORDER[a.status] - ORDER[b.status] || a.name.localeCompare(b.name),
  );
  const quarantined = health?.config?.quarantined ?? [];
  const failing = providers.filter((p) => p.status === "error" || p.status === "degraded");
  // The header dot is the worst thing on the page, not just whether HUD's own process is up.
  const state: TileState =
    error || providers.some((p) => p.status === "error")
      ? "down"
      : health?.status === "degraded" || failing.length > 0 || quarantined.length > 0
        ? "degraded"
        : health
          ? "up"
          : "unknown";
  const summary = [
    health ? `v${health.app_version}` : null,
    health?.uptime_seconds !== undefined ? `up ${formatUptime(health.uptime_seconds)}` : null,
    health?.config ? `config ${health.config.version}` : null,
    health?.db ? `database ${formatBytes(health.db.size_bytes)}` : null,
    fetchedAt ? `checked ${fetchedAt.toLocaleTimeString()}` : null,
  ].filter(Boolean);
  return (
    <>
      <header className="board__header">
        <h1 className="board__title">
          <span className="status-dot" data-state={state} aria-label={state} /> System
        </h1>
        <span className="board__meta">{summary.join(" · ")}</span>
      </header>
      {error || health?.config?.error ? (
        <p className="system__alert" role="alert">
          {error ?? health?.config?.error}
        </p>
      ) : null}
      <div className="system">
        {quarantined.length > 0 ? (
          // Invalid files (invariant 6): a board set aside this way reaches no user's
          // sidebar, so this is where an operator finds out.
          <section className="widget system__section" aria-label="Invalid config files">
            <h2 className="widget__title">Invalid config files</h2>
            <ul className="list">
              {quarantined.map((q) => (
                <li key={q.file} className="list__item">
                  <span className="status-dot" data-state={q.serving_last_good ? "degraded" : "down"} />
                  <div className="list__main">
                    <span className="list__name">{q.file}</span>
                    <span className="list__sub system__error">{q.issues.join(" · ")}</span>
                  </div>
                  <span className="list__field list__state" data-state={q.serving_last_good ? "degraded" : "down"}>
                    {q.serving_last_good ? "last valid version" : "not loaded"}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        <section className="widget system__section" aria-label="Providers">
          <h2 className="widget__title">
            <span className="widget__title-text">
              Providers · {providers.length}
              {failing.length > 0 ? ` · ${failing.length} failing` : ""}
            </span>
          </h2>
          <div className="widget__body">
            {health && !health.providers ? (
              <p className="list__empty">
                Provider health needs the <code>providers:view</code> permission.
              </p>
            ) : providers.length === 0 && health ? (
              <p className="list__empty">No providers configured. Add one under /config/providers/.</p>
            ) : (
              <ul className="list">
                {providers.map((p) => {
                  const bad = p.status === "error" || p.status === "degraded";
                  return (
                    <li key={p.name} className="list__item">
                      <span className="status-dot" data-state={PROVIDER_STATE[p.status]} aria-label={p.status} />
                      <div className="list__main">
                        <span className="list__name">{p.name}</span>
                        {bad && p.last_error ? (
                          <span className="list__sub system__error" title={p.last_error}>
                            {p.last_error}
                          </span>
                        ) : (
                          <span className="list__sub">{p.tier}</span>
                        )}
                      </div>
                      {bad ? (
                        <span className="list__field list__state" data-state={PROVIDER_STATE[p.status]}>
                          {p.status}
                        </span>
                      ) : null}
                      <div className="list__values">
                        <span className="list__field" title="resources">
                          {p.resource_count} resources
                        </span>
                        <span className="list__field" title="last successful poll">
                          {bad ? `last ok ${formatAge(p.last_success)}` : formatAge(p.last_success)}
                        </span>
                        {p.circuit_open_until ? (
                          <span className="list__field" title="circuit breaker">
                            paused until {new Date(p.circuit_open_until).toLocaleTimeString()}
                          </span>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </section>
      </div>
    </>
  );
}
