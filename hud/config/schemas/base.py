# SPDX-License-Identifier: Apache-2.0
"""Shared document envelope: ``apiVersion`` / ``kind`` / ``metadata`` / ``spec``."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

API_VERSION = "hud/v1"


class Metadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    title: str | None = None


class Document(BaseModel):
    """Envelope keys are closed; ``spec`` models opt into ``extra="allow"`` individually so
    unknown keys are preserved and warned about rather than refused (PLAN.md §11.3)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    api_version: Literal["hud/v1"] = Field(alias="apiVersion")
    kind: str
    metadata: Metadata | None = None
