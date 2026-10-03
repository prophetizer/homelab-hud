# SPDX-License-Identifier: Apache-2.0
"""Every response HUD sends carries ``X-HUD: 1``.

A login gate in front of HUD — Authelia behind Traefik's forward auth — answers some of
the browser's API calls itself once its own session lapses: a redirect to its portal, or
a 401/403. The marker is how the web app tells those from HUD's own 401 (sign in to HUD)
and reloads into the gate's sign-in instead (web/src/api/client.ts). Pure ASGI, so it
adds one header and nothing else.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = (b"x-hud", b"1")


class MarkResponses:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def marked(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), HEADER]
            await send(message)

        await self.app(scope, receive, marked)


__all__ = ["HEADER", "MarkResponses"]
