# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/reports`` — the reports configured, their files, and "run now" (PLAN.md §9).

A report reads history across every provider, so it is gated by its own permissions:
``reports:view`` to list and open, ``reports:run`` to run one now (admins hold both via
``*``). Files are served only when they are this report's own, named the default way, and
the HTML with a CSP that allows its inline styles and nothing else — no scripts, no fetches.

Board export (§9.5): ``GET /reports/adhoc/plan`` says what an export holds;
``GET /reports/adhoc`` renders any board the caller can see as PDF or
CSV over 24h, 7d or 30d, written nowhere — a GET, so the browser opens the PDF in its own
viewer; it changes nothing. ``POST /reports/from-board`` saves a board as a weekly report
file, which needs ``reports:edit`` (admins only, via ``*``, unless granted).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Annotated, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from hud.api import deps
from hud.api.boards import _find, _model
from hud.config import ConfigError
from hud.reporting import board_export
from hud.reporting.runner import ReportRunner, RunResult, reports

router = APIRouter(tags=["reports"])

HTML_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"
# A pdf takes a CPU for seconds; two at once is plenty for a homelab, the rest wait.
_EXPORTS = asyncio.Semaphore(2)


class ReportFile(BaseModel):
    name: str
    format: str
    size: int
    modified: datetime


class LastRun(BaseModel):
    at: datetime
    seconds: float
    files: list[str]
    skipped: list[str]
    section_notes: dict[str, str]
    error: str | None


class ReportInfo(BaseModel):
    name: str
    title: str
    schedule: str
    timezone: str
    window: str
    sections: list[str]
    outputs: list[str]
    next_run: datetime | None
    last_run: LastRun | None
    files: list[ReportFile]


class ReportList(BaseModel):
    reports: list[ReportInfo]


def _runner(request: Request) -> ReportRunner:
    return cast("ReportRunner", request.app.state.reports)


def _last(result: RunResult | None) -> LastRun | None:
    if result is None:
        return None
    return LastRun(
        at=datetime.fromtimestamp(result.started).astimezone(),
        seconds=result.seconds,
        files=result.files,
        skipped=result.skipped,
        section_notes=result.section_notes,
        error=result.error,
    )


@router.get("/reports")
async def list_reports(request: Request) -> ReportList:
    await deps.require(request, "reports:view")
    runner = _runner(request)
    out = []
    for name, doc in sorted(reports(deps.config(request).snapshot).items()):
        files = [
            ReportFile(
                name=p.name,
                format=p.suffix.lstrip("."),
                size=p.stat().st_size,
                modified=datetime.fromtimestamp(p.stat().st_mtime).astimezone(),
            )
            for p in runner.files(name)
        ]
        out.append(
            ReportInfo(
                name=name,
                title=doc.metadata.title or name,
                schedule=doc.spec.schedule,
                timezone=runner.timezone(doc),
                window=doc.spec.window.from_,
                sections=[s.title for s in doc.spec.sections],
                outputs=[o.format for o in doc.spec.outputs],
                next_run=runner.next_run(name),
                last_run=_last(runner.last.get(name)),
                files=files,
            )
        )
    return ReportList(reports=out)


@router.post("/reports/{name}/run")
async def run_report(request: Request, name: str) -> LastRun:
    await deps.require(request, "reports:run")
    runner = _runner(request)
    if name not in reports(deps.config(request).snapshot):
        raise HTTPException(status_code=404, detail=f"no report {name!r}")
    result = await runner.run(name)
    last = _last(result)
    assert last is not None
    return last


@router.get("/reports/{name}/files/{file_name}")
async def get_report_file(request: Request, name: str, file_name: str) -> Response:
    await deps.require(request, "reports:view")
    path = next((p for p in _runner(request).files(name) if p.name == file_name), None)
    if path is None:
        raise HTTPException(status_code=404, detail="no such report file")
    body = path.read_bytes()
    if path.suffix == ".html":
        return Response(
            content=body,
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Security-Policy": HTML_CSP,
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-cache",
            },
        )
    if path.suffix == ".pdf":
        # Inline, for the browser's own viewer; no sandbox CSP, which would block it.
        return Response(
            content=body,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{path.name}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-cache",
            },
        )
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{path.name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


class ExportPlan(BaseModel):
    sections: list[str]
    left_out: list[str]


@router.get("/reports/adhoc/plan")
async def export_plan(request: Request, board: str) -> ExportPlan:
    """What an export of ``board`` would hold, without reading history: for the menu."""
    p = await deps.require(request, "reports:view")
    found = _find(request, p, board)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no board {board!r}")
    plan = board_export.plan(_model(found), _runner(request).cache)
    return ExportPlan(sections=[s["title"] for s in plan.sections], left_out=plan.left_out)


@router.get("/reports/adhoc")
async def export_board(
    request: Request,
    board: str,
    span: Annotated[board_export.Range, Query(alias="range")] = "7d",
    fmt: Annotated[board_export.Format, Query(alias="format")] = "pdf",
) -> Response:
    """A board as a report over the last ``range``, in ``format``, rendered now."""
    p = await deps.require(request, "reports:view")
    found = _find(request, p, board)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no board {board!r}")
    runner = _runner(request)
    plan = board_export.plan(_model(found), runner.cache)
    if not plan.sections:
        raise HTTPException(status_code=422, detail="nothing on this board has history to export")
    tz = ZoneInfo(deps.config(request).snapshot.settings.spec.timezone)
    window = board_export.window_for(span, time.time(), tz)
    async with _EXPORTS:
        try:
            body = await asyncio.to_thread(
                board_export.export, runner.engine, runner.cache, plan, window, fmt
            )
        except Exception as exc:  # the pdf renderer's own failure, said as it is
            detail = f"{fmt} could not be rendered: {type(exc).__name__}: {exc}"
            raise HTTPException(status_code=500, detail=detail) from exc
    date = datetime.fromtimestamp(window.until, tz).date().isoformat()
    file_name = f"{board}-{span}-{date}.{fmt}"
    if fmt == "pdf":
        media, disposition = "application/pdf", "inline"
    else:
        media, disposition = "text/csv; charset=utf-8", "attachment"
    return Response(
        content=body,
        media_type=media,
        headers={
            "Content-Disposition": f'{disposition}; filename="{file_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


class FromBoard(BaseModel):
    board: str


class SavedReport(BaseModel):
    report: str
    file: str
    left_out: list[str]
    next_run: datetime | None


@router.post("/reports/from-board", status_code=201)
async def save_board_as_report(request: Request, body: FromBoard) -> SavedReport:
    """Write ``reports/<board>.yaml``: the board's sections, weekly. Refused (409) when a
    report of that name exists — that file is edited, never replaced."""
    p = await deps.require(request, "reports:edit")
    found = _find(request, p, body.board)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no board {body.board!r}")
    model = _model(found)
    name = model.metadata.name
    config = deps.config(request)
    relative = f"reports/{name}.yaml"
    taken = f"a report named {name!r} already exists ({relative}); edit that file instead"
    if name in reports(config.snapshot) or (config.config_dir / relative).exists():
        raise HTTPException(status_code=409, detail=taken)
    runner = _runner(request)
    plan = board_export.plan(model, runner.cache)
    if not plan.sections:
        raise HTTPException(status_code=422, detail="nothing on this board has history to report")
    text = board_export.report_text(model, plan, datetime.now().astimezone())
    try:
        await config.create_async(relative, text)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=taken) from exc
    except (ConfigError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await asyncio.to_thread(
        deps.auth(request).store.audit, p.subject, "reports.create", name, "ok", {"file": relative}
    )
    return SavedReport(
        report=name, file=relative, left_out=plan.left_out, next_run=runner.next_run(name)
    )
