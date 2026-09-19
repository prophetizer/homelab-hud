# SPDX-License-Identifier: Apache-2.0
"""Process environment (``HUD_*``). Everything authored lives in ``/config``; this is only
where to find it and what port to listen on."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class HudEnv(BaseSettings):
    # extra="ignore": HUD_SECRET_<NAME> variables share the prefix and are read elsewhere.
    model_config = SettingsConfigDict(env_prefix="HUD_", extra="ignore")

    config_dir: Path = Path("/config")
    data_dir: Path = Path("/data")
    host: str = "0.0.0.0"  # noqa: S104 — container-internal bind; port publishing is compose's job
    port: int = 8080
    config_poll_interval: float = 2.0
    log_level: str = "INFO"
    # Built SPA. Defaults to the in-repo build output; the Docker image sets it explicitly.
    static_dir: Path = Path(__file__).resolve().parent.parent / "web" / "dist"
