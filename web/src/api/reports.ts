// SPDX-License-Identifier: Apache-2.0
import { getJson, sendJson } from "./client";

export interface ReportFile {
  name: string;
  format: "html" | "csv" | "pdf" | string;
  size: number;
  modified: string;
}
export interface LastRun {
  at: string;
  seconds: number;
  files: string[];
  skipped: string[];
  section_notes: Record<string, string>;
  error: string | null;
}
export interface ReportInfo {
  name: string;
  title: string;
  schedule: string;
  timezone: string;
  window: string;
  sections: string[];
  outputs: string[];
  next_run: string | null;
  last_run: LastRun | null;
  files: ReportFile[];
}

export function fetchReports(signal?: AbortSignal): Promise<{ reports: ReportInfo[] }> {
  return getJson("/api/v1/reports", signal);
}

export function runReport(name: string): Promise<LastRun> {
  return sendJson("POST", `/api/v1/reports/${encodeURIComponent(name)}/run`);
}

export function reportFileUrl(name: string, file: string): string {
  return `/api/v1/reports/${encodeURIComponent(name)}/files/${encodeURIComponent(file)}`;
}

/** The runs on disk, grouped by date (a run writes one file per format). */
export function runsByDate(files: ReportFile[], name: string): { date: string; files: ReportFile[] }[] {
  const out = new Map<string, ReportFile[]>();
  for (const f of files) {
    const date = f.name.slice(name.length + 1, name.length + 11);
    out.set(date, [...(out.get(date) ?? []), f]);
  }
  const rank = (f: ReportFile) => FORMAT_ORDER.indexOf(f.format) >>> 0; // unknown formats last
  return [...out.entries()]
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([date, fs]) => ({ date, files: [...fs].sort((a, b) => rank(a) - rank(b)) }));
}

/** One run's links always read View, PDF, CSV — whichever file was written last. */
const FORMAT_ORDER = ["html", "pdf", "csv"];
