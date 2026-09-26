# SPDX-License-Identifier: Apache-2.0
"""Provider-side failures. Every one carries the provider name so a tile can show it."""

from __future__ import annotations

from hud.config.redact import redact


class ProviderError(Exception):
    """Base for anything a provider raises on purpose. The message is user-facing."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        # User-facing, so never carries a secret — e.g. an httpx error quoting a URL whose
        # query holds an API key. See hud.config.redact.
        self.message = redact(message)
        super().__init__(f"{provider}: {self.message}")


class ProviderBuildError(ProviderError):
    """The document validated but the provider could not be instantiated — a missing
    environment variable or secret, an unloadable plugin. Surfaced in health, not fatal."""


class ProviderPollError(ProviderError):
    """One poll failed. The scheduler records it and decides about the circuit breaker."""
