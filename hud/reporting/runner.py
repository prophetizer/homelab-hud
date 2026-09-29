# SPDX-License-Identifier: Apache-2.0
"""Report runner (PLAN.md §9): scheduled and on-demand runs, files under /data/reports.

Each ``Report`` document is scheduled on its cron ``schedule`` in its timezone (settings'
when unset) and re-scheduled whenever config reloads. A run reads the window, builds every
section (one failing section is that section's note, never a failed report), and writes
each output it can: html and csv now. pdf and webhook outputs are listed in the report's
footer and the run result as not produced yet — never silently dropped.

Files are named ``{name}-{date}.{ext}`` (the run's local date) unless an output's ``path``
says otherwise; a path always resolves inside the reports directory. Only files named the
default way are listed and served.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import Engine

from hud import __version__
from hud.collector import LiveCache
from hud.config import ConfigManager, ConfigSnapshot
from hud.config.schemas.report import Output, ReportDocument
from hud.reporting import render
from hud.reporting.sections import SectionResult, Window, build

log = logging.getLogger(__name__)

EXTENSIONS = {"html": "html", "csv": "csv", "pdf": "pdf"}
NOT_YET = {"pdf": "pdf (arrives with the next build)", "webhook": "webhook (not built yet)"}


@dataclass
class RunResult:
    name: str
    started: float
    seconds: float = 0.0
    files: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    section_notes: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def reports(snapshot: ConfigSnapshot) -> dict[str, ReportDocument]:
    return {
        d.model.metadata.name: d.model
        for d in snapshot.documents
        if isinstance(d.model, ReportDocument)
    }


class ReportRunner:
    def __init__(
        self,
        engine: Engine,
        cache: LiveCache,
        config: ConfigManager,
        out_dir: Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.engine = engine
        self.cache = cache
        self.config = config
        self.out_dir = out_dir
        self.clock = clock
        self.last: dict[str, RunResult] = {}
        self._scheduler: AsyncIOScheduler | None = None
        self._running: set[str] = set()

    # ---------------------------------------------------------------- scheduling

    def start(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._scheduler.start()
        self.schedule(self.config.snapshot)

    def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def schedule(self, snapshot: ConfigSnapshot) -> None:
        """Replace every report job with the reports in ``snapshot``."""
        if self._scheduler is None:
            return
        self._scheduler.remove_all_jobs()
        for name, doc in reports(snapshot).items():
            tz = ZoneInfo(self.timezone(doc))
            self._scheduler.add_job(
                self.run,
                CronTrigger.from_crontab(doc.spec.schedule, timezone=tz),
                args=[name],
                id=f"report:{name}",
                name=f"report {name}",
                coalesce=True,
                max_instances=1,
                misfire_grace_time=3600,
            )

    async def reconcile(self, snapshot: ConfigSnapshot) -> None:
        self.schedule(snapshot)

    def next_run(self, name: str) -> datetime | None:
        if self._scheduler is None:
            return None
        job = self._scheduler.get_job(f"report:{name}")
        return job.next_run_time if job is not None else None

    def timezone(self, doc: ReportDocument) -> str:
        return doc.spec.timezone or self.config.snapshot.settings.spec.timezone

    # ---------------------------------------------------------------- running

    async def run(self, name: str) -> RunResult:
        """Run one report now. Two runs of one report never overlap."""
        doc = reports(self.config.snapshot).get(name)
        started = self.clock()
        if doc is None:
            return RunResult(name, started, error=f"no report {name!r}")
        if name in self._running:
            return RunResult(name, started, error="already running")
        self._running.add(name)
        try:
            result = await asyncio.to_thread(self._run, doc, started)
        except Exception as exc:
            log.exception("report %s failed", name)
            result = RunResult(name, started, error=f"{type(exc).__name__}: {exc}")
        finally:
            self._running.discard(name)
        result.seconds = self.clock() - started
        self.last[name] = result
        log.info(
            "report %s: %s in %.1fs%s",
            name,
            ", ".join(result.files) or "nothing written",
            result.seconds,
            f" (not produced: {', '.join(result.skipped)})" if result.skipped else "",
        )
        return result

    def _run(self, doc: ReportDocument, started: float) -> RunResult:
        tz = ZoneInfo(self.timezone(doc))
        until = int(started)
        window = Window(until - doc.spec.window.seconds, until, tz)
        sections: list[SectionResult] = [
            build(s, self.engine, self.cache, window) for s in doc.spec.sections
        ]
        result = RunResult(doc.metadata.name, started)
        result.skipped = [NOT_YET[o.format] for o in doc.spec.outputs if o.format in NOT_YET]
        result.section_notes = {s.title: s.note for s in sections if s.note}
        meta = render.ReportMeta(
            title=doc.metadata.title or doc.metadata.name,
            since=window.since,
            until=window.until,
            tz=tz,
            version=__version__,
            skipped=result.skipped,
        )
        date = datetime.fromtimestamp(until, tz).date().isoformat()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for out in doc.spec.outputs:
            if out.format == "html":
                body = render.html(meta, sections)
            elif out.format == "csv":
                body = render.csv_text(sections, tz)
            else:
                continue
            path = self._path(out, doc.metadata.name, date)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(path)
            result.files.append(path.name)
        return result

    def _path(self, out: Output, name: str, date: str) -> Path:
        """The output's file, always directly inside the reports directory."""
        ext = EXTENSIONS[out.format]
        if out.path:
            raw = out.path.replace("{{ name }}", name).replace("{{ date }}", date)
            file_name = Path(raw).name  # /data/reports/x.html, or x.html: the name alone
        else:
            file_name = f"{name}-{date}.{ext}"
        return self.out_dir / file_name

    # ---------------------------------------------------------------- files

    def files(self, name: str) -> list[Path]:
        """This report's output files, newest first."""
        if not self.out_dir.is_dir():
            return []
        # Exactly name-YYYY-MM-DD.ext: "weekly" must not list "weekly-lab"'s files.
        pattern = re.compile(rf"{re.escape(name)}-\d{{4}}-\d{{2}}-\d{{2}}\.(html|csv)")
        found = [p for p in self.out_dir.iterdir() if p.is_file() and pattern.fullmatch(p.name)]
        return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


__all__ = ["ReportRunner", "RunResult", "reports"]
