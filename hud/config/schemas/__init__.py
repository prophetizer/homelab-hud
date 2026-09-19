# SPDX-License-Identifier: Apache-2.0
"""``kind`` → document schema. Report and RBAC arrive with the phases that implement them."""

from hud.config.schemas.base import API_VERSION, Document, Metadata
from hud.config.schemas.board import (
    BoardDocument,
    BoardSpec,
    EmbedWidget,
    ListWidget,
    MetricWidget,
    ResourceWidget,
    StaticWidget,
    UnsupportedWidget,
    Widget,
)
from hud.config.schemas.provider import ProviderDocument, ProviderSpec, ResourceSpec
from hud.config.schemas.settings import (
    DEFAULT_SETTINGS_YAML,
    Retention,
    SettingsDocument,
    SettingsSpec,
    parse_duration,
)

KIND_SCHEMAS: dict[str, type[Document]] = {
    "Settings": SettingsDocument,
    "Provider": ProviderDocument,
    "Board": BoardDocument,
}

__all__ = [
    "API_VERSION",
    "DEFAULT_SETTINGS_YAML",
    "KIND_SCHEMAS",
    "BoardDocument",
    "BoardSpec",
    "Document",
    "EmbedWidget",
    "ListWidget",
    "Metadata",
    "MetricWidget",
    "ProviderDocument",
    "ProviderSpec",
    "ResourceSpec",
    "ResourceWidget",
    "Retention",
    "SettingsDocument",
    "SettingsSpec",
    "StaticWidget",
    "UnsupportedWidget",
    "Widget",
    "parse_duration",
]
