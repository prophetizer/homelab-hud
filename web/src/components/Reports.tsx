// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { type Me, hasPermission } from "../api/auth";
import { formatAge, formatValue } from "../api/format";
import { fetchReports, reportFileUrl, runReport, runsByDate } from "../api/reports";
import { usePoll } from "../hooks/usePoll";

const POLL_MS = 30_000;

function when(iso: string | null, timeZone: string): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      timeZone,
      weekday: "short",
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return new Date(iso).toLocaleString();
  }
}

/** Reports (PLAN §9): what is configured, when each runs next, the last run, its files. */
export function Reports({ me }: { me: Me }) {
  const { data, error, refresh } = usePoll(`reports:${me.subject}`, fetchReports, POLL_MS);
  const [running, setRunning] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const canRun = hasPermission(me, "reports:run");
  const run = async (name: string) => {
    setRunning(name);
    setRunError(null);
    try {
      const r = await runReport(name);
      if (r.error) setRunError(`${name}: ${r.error}`);
    } catch (e) {
      setRunError(`${name}: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRunning(null);
      refresh();
    }
  };
  return (
    <>
      <header className="board__header">
        <h1 className="board__title">Reports</h1>
        <span className="board__meta">files under /data/reports · definitions in /config/reports/</span>
      </header>
      {error ? (
        <p className="board-status" role="alert">
          reports: {error}
        </p>
      ) : null}
      {runError ? (
        <p className="board-status reports__error" role="alert">
          {runError}
        </p>
      ) : null}
      {data && data.reports.length === 0 ? (
        <p className="board-status">No reports yet — add one under /config/reports/ (see templates/reports/).</p>
      ) : null}
      <div className="reports">
        {data?.reports.map((r) => {
          const last = r.last_run;
          return (
            <section key={r.name} className="widget report" aria-label={r.title}>
              <div className="report__head">
                <h2 className="report__title">{r.title}</h2>
                {canRun ? (
                  <button
                    type="button"
                    className="report__run"
                    disabled={running !== null}
                    onClick={() => void run(r.name)}
                  >
                    {running === r.name ? "Running…" : "Run now"}
                  </button>
                ) : null}
              </div>
              <dl className="report__facts">
                <dt>Next run</dt>
                <dd>
                  {when(r.next_run, r.timezone)} <span className="report__faint">({r.schedule})</span>
                </dd>
                <dt>Covers</dt>
                <dd>the {r.window.replace("-", "")} before each run</dd>
                <dt>Sections</dt>
                <dd>{r.sections.join(" · ")}</dd>
                {last ? (
                  <>
                    <dt>Last run</dt>
                    <dd>
                      {formatAge(last.at)} in {formatValue(last.seconds, "seconds")}
                      {last.error ? <span className="report__bad"> · failed: {last.error}</span> : null}
                      {last.skipped.length > 0 ? (
                        <span className="report__faint"> · not produced: {last.skipped.join(", ")}</span>
                      ) : null}
                    </dd>
                    {Object.entries(last.section_notes).map(([title, note]) => (
                      <dd key={title} className="report__note">
                        {title}: {note}
                      </dd>
                    ))}
                  </>
                ) : null}
              </dl>
              {r.files.length > 0 ? (
                <ul className="report__runs">
                  {runsByDate(r.files, r.name).map((run) => (
                    <li key={run.date} className="report__run-row">
                      <span className="report__date">{run.date}</span>
                      {run.files.map((f) => (
                        <a
                          key={f.name}
                          href={reportFileUrl(r.name, f.name)}
                          target={f.format === "csv" ? undefined : "_blank"}
                          rel="noreferrer"
                          download={f.format === "csv" ? f.name : undefined}
                        >
                          {f.format === "html" ? "View" : f.format.toUpperCase()}
                        </a>
                      ))}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="report__faint">No files yet{canRun ? " — Run now makes the first." : "."}</p>
              )}
            </section>
          );
        })}
      </div>
    </>
  );
}
