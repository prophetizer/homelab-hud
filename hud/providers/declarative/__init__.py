# SPDX-License-Identifier: Apache-2.0
"""Tier 1 — declarative providers from YAML alone (PLAN.md §7.1)."""

from hud.providers.declarative.engine import DeclarativeProvider, build_declarative

__all__ = ["DeclarativeProvider", "build_declarative"]
