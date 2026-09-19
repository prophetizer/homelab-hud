# SPDX-License-Identifier: Apache-2.0
import pytest

from hud.config.interpolate import MissingEnvVarError, env_refs, interpolate_env


def test_substitutes_and_lists_refs() -> None:
    env = {"HA_BASE_URL": "http://ha.lab:8123", "X": "1"}
    text = "${HA_BASE_URL}/history?x=${X}&again=${X}"
    assert interpolate_env(text, env) == "http://ha.lab:8123/history?x=1&again=1"
    assert env_refs(text) == ["HA_BASE_URL", "X"]


def test_missing_var_raises_rather_than_blanking() -> None:
    with pytest.raises(MissingEnvVarError, match="'NOPE'"):
        interpolate_env("${NOPE}/api", {})


def test_secret_refs_are_never_touched() -> None:
    text = "${secret:token}"
    assert interpolate_env(text, {"secret:token": "leak", "token": "leak"}) == text
    assert env_refs(text) == []
