# SPDX-License-Identifier: Apache-2.0
"""``GET /api/v1/boards`` and ``GET /api/v1/boards/{name}`` (PLAN.md Appendix A).

``visible_to`` is carried in the document but not enforced here: RBAC arrives with auth
in Phase 1b, and until then every board is visible to the single implicit operator.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from hud.api import deps
from hud.config.schemas import BoardDocument
from hud.widgets import BoardSummary, ResolvedBoard

router = APIRouter(tags=["boards"])


class BoardList(BaseModel):
    boards: list[BoardSummary]


def _boards(request: Request) -> list[BoardDocument]:
    docs = [d.model for d in deps.config(request).snapshot.documents]
    return sorted((d for d in docs if isinstance(d, BoardDocument)), key=lambda d: d.metadata.name)


@router.get("/boards", response_model=BoardList)
async def list_boards(request: Request) -> BoardList:
    engine = deps.widgets(request)
    return BoardList(boards=[engine.summary(d) for d in _boards(request)])


@router.get("/boards/{name}", response_model=ResolvedBoard)
async def get_board(request: Request, name: str) -> ResolvedBoard:
    for doc in _boards(request):
        if doc.metadata.name == name:
            return await deps.widgets(request).resolve_board(doc, datetime.now(UTC))
    raise HTTPException(status_code=404, detail=f"no board {name!r}")
