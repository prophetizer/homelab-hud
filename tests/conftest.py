# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

from hud.config import ConfigManager

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    d = tmp_path / "config"
    d.mkdir()
    return d


@pytest.fixture
def manager(config_dir: Path) -> ConfigManager:
    return ConfigManager(config_dir, poll_interval=0.05)
