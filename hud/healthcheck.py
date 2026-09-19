# SPDX-License-Identifier: Apache-2.0
"""Container HEALTHCHECK: ``python -m hud.healthcheck``. Exit 0 when the API answers 200.

A ``degraded`` status (bad config edit after a good start) is still healthy for the
container's purposes — the process is up and serving the last good configuration.
"""

import json
import sys
import urllib.error
import urllib.request

from hud.settings import HudEnv


def main() -> int:
    env = HudEnv()
    url = f"http://127.0.0.1:{env.port}/api/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1
    print(f"{body.get('status')} config={body.get('config', {}).get('version')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
