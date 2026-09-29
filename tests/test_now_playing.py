# SPDX-License-Identifier: Apache-2.0
"""Now playing: the same playback words on every media server, why a stream transcodes,
and its resolution — from each server's own session shape."""

import json
from pathlib import Path

import httpx
import respx

from hud.config import SecretResolver
from hud.providers import ProviderContext
from hud.providers.declarative import build_declarative
from tests.test_templates import BASE, TEMPLATES, _load

SECRETS = {"plex": "PLEX_TOKEN", "emby": "EMBY_API_KEY", "jellyfin": "JELLYFIN_API_KEY"}


async def _sessions(config_dir: Path, service: str) -> dict[str, dict]:
    own = config_dir.parent / f"config-{service}"  # one provider per config dir
    own.mkdir(exist_ok=True)
    doc = _load(own, TEMPLATES / f"{service}.yaml")
    env = {f"{service.upper()}_BASE_URL": BASE, f"HUD_SECRET_{SECRETS[service]}": "t"}
    ctx = ProviderContext.create(service, SecretResolver(own, own / "none", env=env), env)
    provider = build_declarative(doc, ctx)
    res = next(r for r in doc.spec.resources if r.name == "sessions")
    body = json.loads((TEMPLATES / "fixtures" / service / "sessions.json").read_text())
    await provider.startup()
    try:
        with respx.mock() as mock:
            mock.route(method="GET", path=res.request.path).mock(
                return_value=httpx.Response(200, json=body)
            )
            result = await provider.poll("sessions")
    finally:
        await provider.shutdown()
    return {r.name: r.attrs for r in result.resources}


async def test_plex_playback_reason_and_resolution(config_dir: Path) -> None:
    by = {name.split(" — ")[-1]: a for name, a in (await _sessions(config_dir, "plex")).items()}
    assert (by["Arrival"]["playback"], by["Arrival"]["resolution"]) == ("Direct play", "4K")
    assert by["Arrival"]["transcode_reason"] is None
    assert by["Strange Dogs"]["playback"] == "Transcode"
    assert by["Strange Dogs"]["transcode_reason"] == "video"
    assert by["Strange Dogs"]["resolution"] == "1080p"
    assert by["Severance"]["transcode_reason"] == "video, audio, subtitles burned in · hardware"


async def test_emby_and_jellyfin_say_it_the_same_way(config_dir: Path) -> None:
    for service in ("emby", "jellyfin"):
        by = {n.split(" — ")[-1]: a for n, a in (await _sessions(config_dir, service)).items()}
        assert (by["Arrival"]["playback"], by["Arrival"]["resolution"]) == ("Direct play", "4K")
        assert by["Arrival"]["transcode_reason"] is None
        assert by["Strange Dogs"]["playback"] == "Transcode"
        assert by["Strange Dogs"]["resolution"] == "720p"
        reasons = by["Strange Dogs"]["transcode_reason"]
        assert reasons == "VideoCodecNotSupported, SubtitleCodecNotSupported"
