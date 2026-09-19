# SPDX-License-Identifier: Apache-2.0
"""``kind`` → document schema. Phase 0 knows ``Settings`` only; Board/Provider/Report/RBAC
arrive with the phases that implement them."""

from hud.config.schemas.base import API_VERSION, Document, Metadata
from hud.config.schemas.settings import (
    DEFAULT_SETTINGS_YAML,
    Retention,
    SettingsDocument,
    SettingsSpec,
    parse_duration,
)

KIND_SCHEMAS: dict[str, type[Document]] = {
    "Settings": SettingsDocument,
}

__all__ = [
    "API_VERSION",
    "DEFAULT_SETTINGS_YAML",
    "KIND_SCHEMAS",
    "Document",
    "Metadata",
    "Retention",
    "SettingsDocument",
    "SettingsSpec",
    "parse_duration",
]
