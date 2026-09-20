# SPDX-License-Identifier: Apache-2.0
"""``GET /api/v1/boards`` and ``GET /api/v1/boards/{name}`` (PLAN.md Appendix A).

A board the caller cannot view never enters a response: the list omits it and the detail
route answers 404, not 403, so its existence is not disclosed (PLAN.md §10.2).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from hud.api import deps
from hud.widgets import BoardSummary, ResolvedBoard

router = APIRouter(tags=["boards"])


class BoardList(BaseModel):
    boards: list[BoardSummary]


@router.get("/boards", response_model=BoardList)
async def list_boards(request: Request) -> BoardList:
    p = await deps.principal(request)
    engine = deps.widgets(request)
    return BoardList(boards=[engine.summary(d) for d in deps.visible_boards(request, p)])


@router.get("/boards/{name}", response_model=ResolvedBoard)
async def get_board(request: Request, name: str) -> ResolvedBoard:
    p = await deps.principal(request)
    for doc in deps.visible_boards(request, p):
        if doc.metadata.name == name:
            return await deps.widgets(request).resolve_board(doc, datetime.now(UTC))
    raise HTTPException(status_code=404, detail=f"no board {name!r}")
