// SPDX-License-Identifier: Apache-2.0
import { fetchBoards } from "./api/client";
import { fetchHealth } from "./api/health";
import { BoardView } from "./components/Board";
import { Overview } from "./components/Overview";
import { Sidebar } from "./components/Sidebar";
import { usePoll } from "./hooks/usePoll";
import { useRoute } from "./router";

const HEALTH_MS = 15_000;
const BOARDS_MS = 30_000;

export function App() {
  const route = useRoute();
  const health = usePoll("health", fetchHealth, HEALTH_MS);
  const boards = usePoll("boards", fetchBoards, BOARDS_MS);
  const providers = health.data?.providers ?? [];
  const unhealthy = providers.filter((p) => p.status === "degraded" || p.status === "error").length;

  return (
    <div className="shell">
      <Sidebar boards={boards.data?.boards ?? null} boardsError={boards.error} route={route} />
      <main className="main">
        {route.kind === "overview" ? (
          <Overview health={health.data} error={health.error} fetchedAt={health.fetchedAt} />
        ) : route.kind === "board" ? (
          <BoardView name={route.name} />
        ) : (
          <div className="board-status" role="alert">
            Nothing at {route.path}.
          </div>
        )}
      </main>
      <footer className="footer">
        <span>config {health.data?.config.version ?? "—"}</span>
        <span>v{health.data?.app_version ?? "—"}</span>
        <span>
          {providers.length} provider{providers.length === 1 ? "" : "s"}
          {unhealthy > 0 ? <span className="footer__warn"> · {unhealthy} failing</span> : null}
        </span>
        {health.data && health.data.config.warnings > 0 ? <span>{health.data.config.warnings} config warning(s)</span> : null}
        {health.error ? <span className="footer__warn">api: {health.error}</span> : null}
      </footer>
    </div>
  );
}
