# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/reports`` — the reports configured, their files, and "run now" (PLAN.md §9).

A report reads history across every provider, so it is gated by its own permissions:
``reports:view`` to list and open, ``reports:run`` to run one now (admins hold both via
``*``). Files are served only when they are this report's own, named the default way, and
the HTML with a CSP that allows its inline styles and nothing else — no scripts, no fetches.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from hud.api import deps
from hud.reporting.runner import ReportRunner, RunResult, reports

router = APIRouter(tags=["reports"])

HTML_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"


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
