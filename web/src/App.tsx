// SPDX-License-Identifier: Apache-2.0
import { useEffect } from "react";
import { fetchApps, fetchBoards } from "./api/client";
import { fetchHealth } from "./api/health";
import type { Me } from "./api/auth";
import { BoardView } from "./components/Board";
import { CommandPalette } from "./components/CommandPalette";
import { Kiosk } from "./components/Kiosk";
import { Login } from "./components/Login";
import { Overview } from "./components/Overview";
import { Sidebar } from "./components/Sidebar";
import { Workspace } from "./components/Workspace";
import { usePoll } from "./hooks/usePoll";
import { useSession } from "./hooks/useSession";
import { buildNavigation, landingBoard } from "./api/nav";
import { boardPath, replaceRoute, useRoute } from "./router";

const HEALTH_MS = 15_000;
const BOARDS_MS = 30_000;

export function App() {
  const { session, signIn, setUp, signUp, signOut } = useSession();
  if (session.status === "loading") {
    return (
      <div className="gate">
        <p className="board-status" role="status">
          {session.error ? `api: ${session.error}` : "Loading…"}
        </p>
      </div>
    );
  }
  if (session.status === "anonymous") {
    return <Login backends={session.backends} error={session.error} onSignIn={signIn} onSetUp={setUp} onSignUp={signUp} />;
  }
  return <Shell me={session.me} onSignOut={() => void signOut()} />;
}

function Shell({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const route = useRoute();
  // Keyed on the subject so a different sign-in restarts every poll from scratch.
  const health = usePoll(`health:${me.subject}`, fetchHealth, HEALTH_MS);
  const boards = usePoll(`boards:${me.subject}`, fetchBoards, BOARDS_MS);
  const apps = usePoll(`apps:${me.subject}`, fetchApps, BOARDS_MS);
  const providers = health.data?.providers ?? [];
  const unhealthy = providers.filter((p) => p.status === "degraded" || p.status === "error").length;
  const landing = boards.data ? landingBoard(buildNavigation(boards.data.boards, []).boards) : null;

  // "/" is a landing, not a page: go to the Home board, replacing "/" in history.
  useEffect(() => {
    if (route.kind === "home" && landing) replaceRoute(boardPath(landing));
  }, [route.kind, landing]);

  if (route.kind === "kiosk") {
    return <Kiosk me={me} boards={boards.data?.boards ?? null} names={route.boards} every={route.every} />;
  }
  const system = <Overview health={health.data} error={health.error} fetchedAt={health.fetchedAt} />;
  return (
    <div className="shell">
      <CommandPalette boards={boards.data?.boards ?? null} apps={apps.data?.apps ?? null} />
      <Sidebar
        boards={boards.data?.boards ?? null}
        boardsError={boards.error}
        apps={apps.data?.apps ?? null}
        route={route}
        me={me}
        onSignOut={onSignOut}
      />
      <main className={route.kind === "app" ? "main main--workspace" : "main"}>
        {route.kind === "system" ? (
          system
        ) : route.kind === "home" ? (
          boards.data && !landing ? (
            system // no dashboards yet: the System page is the landing
          ) : (
            <div className="board-status">Loading…</div>
          )
        ) : route.kind === "board" ? (
          <BoardView name={route.name} me={me} />
        ) : route.kind === "app" ? (
          <Workspace board={route.board} widget={route.widget} />
        ) : (
          <div className="board-status" role="alert">
            Nothing at {route.path}.
          </div>
        )}
      </main>
      <footer className="footer">
        <span>config {health.data?.config?.version ?? "—"}</span>
        <span>v{health.data?.app_version ?? "—"}</span>
        {health.data?.providers ? (
          <span>
            {providers.length} provider{providers.length === 1 ? "" : "s"}
            {unhealthy > 0 ? <span className="footer__warn"> · {unhealthy} failing</span> : null}
          </span>
        ) : null}
        {health.data?.config && health.data.config.warnings > 0 ? <span>{health.data.config.warnings} config warning(s)</span> : null}
        {health.error ? <span className="footer__warn">api: {health.error}</span> : null}
      </footer>
    </div>
  );
}
