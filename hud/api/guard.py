# SPDX-License-Identifier: Apache-2.0
"""Router-wide CSRF guard for mutating ``/api/v1`` requests.

Every session-bearing principal carries a CSRF token (``GET /auth/me`` returns it); a
POST/PATCH/PUT/DELETE must echo it in ``X-CSRF-Token``. The login-establishing routes are
exempt because there is no principal yet — they take JSON bodies only, which a cross-site
form cannot send without a CORS preflight this app never grants.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from hud.api import deps

MUTATING = frozenset({"POST", "PATCH", "PUT", "DELETE"})
EXEMPT_SUFFIXES = (
    "/auth/login",
    "/auth/setup",
    "/auth/register",
    "/auth/oidc/start",
    "/auth/oidc/callback",
)
HEADER = "X-CSRF-Token"


async def csrf_guard(request: Request) -> None:
    if request.method not in MUTATING:
        return
    if request.url.path.endswith(EXEMPT_SUFFIXES):
        return
    p = await deps.principal(request)
    sent = request.headers.get(HEADER, "")
    if not sent or not hmac.compare_digest(sent, p.csrf_token):
        raise HTTPException(status_code=403, detail=f"missing or stale {HEADER} header")


__all__ = ["HEADER", "csrf_guard"]
