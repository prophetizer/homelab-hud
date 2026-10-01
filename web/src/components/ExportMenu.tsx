// SPDX-License-Identifier: Apache-2.0
import { useEffect, useRef, useState } from "react";
import { type Me, hasPermission } from "../api/auth";
import {
  EXPORT_RANGES,
  type ExportPlan,
  type ExportRange,
  type SavedReport,
  exportUrl,
  fetchExportPlan,
  saveBoardAsReport,
} from "../api/reports";
import { onLinkClick } from "../router";

/** "Export" in a board's header: the board over 24 hours, 7 days or 30 days as a PDF or a
 *  CSV, rendered by the report pipeline and written nowhere — and, for whoever may edit
 *  reports, "Schedule weekly", which saves it once as /config/reports/<board>.yaml. */
export function ExportMenu({ board, me }: { board: string; me: Me }) {
  const [open, setOpen] = useState(false);
  const [range, setRange] = useState<ExportRange>("7d");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<SavedReport | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [plan, setPlan] = useState<ExportPlan | null>(null);
  const [planFailed, setPlanFailed] = useState<string | null>(null);
  const root = useRef<HTMLSpanElement>(null);
  const canSchedule = hasPermission(me, "reports:edit");

  // What the export holds, asked each time the menu opens: the board may have changed.
  useEffect(() => {
    if (!open) return;
    const ctl = new AbortController();
    setPlanFailed(null);
    fetchExportPlan(board, ctl.signal)
      .then(setPlan)
      .catch((e: unknown) => {
        if (!ctl.signal.aborted) setPlanFailed(e instanceof Error ? e.message : String(e));
      });
    return () => ctl.abort();
  }, [open, board]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  const schedule = () => {
    setSaving(true);
    setFailed(null);
    saveBoardAsReport(board)
      .then(setSaved)
      .catch((e: unknown) => setFailed(e instanceof Error ? e.message : String(e)))
      .finally(() => setSaving(false));
  };

  const empty = plan !== null && plan.sections.length === 0;
  return (
    <span className="export" ref={root}>
      <button className="board__edit" type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        Export
      </button>
      {open ? (
        <div className="export__menu" role="dialog" aria-label="Export this board">
          <div className="export__ranges" role="radiogroup" aria-label="Time range">
            {EXPORT_RANGES.map((r) => (
              <button
                key={r.value}
                type="button"
                role="radio"
                aria-checked={range === r.value}
                className="export__range"
                onClick={() => setRange(r.value)}
              >
                {r.label}
              </button>
            ))}
          </div>
          {empty ? (
            <p className="export__note" role="status">
              Nothing on this board has history to export.
            </p>
          ) : (
            <div className="export__actions">
              <a className="export__action" href={exportUrl(board, range, "pdf")} target="_blank" rel="noopener">
                Open PDF
              </a>
              <a className="export__action" href={exportUrl(board, range, "csv")} download>
                Download CSV
              </a>
            </div>
          )}
          {planFailed ? (
            <p className="export__note export__note--error" role="alert">
              {planFailed}
            </p>
          ) : plan && !empty ? (
            <p className="export__note">
              {plan.sections.length} section{plan.sections.length === 1 ? "" : "s"}: {plan.sections.join(", ")}.
              {plan.left_out.length ? <> Not included, no history: {plan.left_out.join(", ")}.</> : null}
            </p>
          ) : null}
          {canSchedule && !empty ? (
            <div className="export__schedule">
              {saved ? (
                <p className="export__note" role="status">
                  Saved as <code>{saved.file}</code>
                  {saved.next_run ? <> · first run {new Date(saved.next_run).toLocaleString()}</> : null}.{" "}
                  <a href="/reports" onClick={onLinkClick}>
                    Reports
                  </a>
                </p>
              ) : (
                <button className="export__action" type="button" disabled={saving} onClick={schedule}>
                  {saving ? "Saving…" : "Schedule weekly"}
                </button>
              )}
              {failed ? (
                <p className="export__note export__note--error" role="alert">
                  {failed}
                </p>
              ) : null}
              {!saved && !failed ? (
                <p className="export__note">Mondays 07:00, the past 7 days, as HTML, CSV and PDF.</p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </span>
  );
}
