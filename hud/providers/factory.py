# SPDX-License-Identifier: Apache-2.0
"""Document → provider instance. The registry calls this for every ``kind: Provider``."""

from __future__ import annotations

from hud.config.schemas import ProviderDocument
from hud.providers.base import Provider, ProviderContext
from hud.providers.declarative import build_declarative


def build_provider(doc: ProviderDocument, ctx: ProviderContext) -> Provider:
    return build_declarative(doc, ctx)
