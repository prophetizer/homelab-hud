# SPDX-License-Identifier: Apache-2.0
"""``python -m hud`` — run the server with settings from the environment."""

import uvicorn

from hud.settings import HudEnv


def main() -> None:
    env = HudEnv()
    uvicorn.run(
        "hud.main:app",
        host=env.host,
        port=env.port,
        log_level=env.log_level.lower(),
        proxy_headers=True,
        server_header=False,
    )


if __name__ == "__main__":
    main()
