# SPDX-License-Identifier: Apache-2.0
"""Every provider's poll state, readable from a shell without signing in.

    docker exec hud python -m hud.providers.status [--name scrutiny] [--json]

The server keeps provider health in memory (it is what ``/api/v1/health`` shows a signed-in
admin). Every 30 s it also writes that list to ``/data/provider-status.json`` — secrets
already masked — and this command reads it: no network, no session, no new way in. A
snapshot older than two minutes is said to be stale, so a stopped HUD never reads as
healthy. URLs and IP addresses in error text are hidden unless ``--show-addresses``.

Exit codes: 0 every provider ok, 1 one or more not ok (failing, circuit open, or could not
be built), 2 no snapshot or a stale one, 3 ``--name`` matches nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hud.config.redact import redact
from hud.config.writer import atomic_write_text
from hud.providers.base import ProviderHealth
from hud.settings import HudEnv

log = logging.getLogger(__name__)

SNAPSHOT_NAME = "provider-status.json"
EVERY_SECONDS = 30
STALE_SECONDS = 120
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\"<>)]+", re.IGNORECASE)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")


# --------------------------------------------------------------------------- server side


def snapshot(providers: Sequence[ProviderHealth], now: datetime) -> str:
    """The providers' health as JSON, with every registered secret masked."""
    rows = []
    for p in providers:
        row = p.model_dump(mode="json")
        if row.get("last_error"):
            row["last_error"] = redact(row["last_error"])
        rows.append(row)
    return json.dumps({"written_at": now.isoformat(), "providers": rows}, indent=1)


async def write_snapshots(
    health: Callable[[], Sequence[ProviderHealth]], path: Path, every: float = EVERY_SECONDS
) -> None:
    """Rewrite ``path`` from ``health()`` every ``every`` seconds, until cancelled."""
    while True:
        try:
            text = snapshot(health(), datetime.now(UTC))
            await asyncio.to_thread(atomic_write_text, path, text)
        except OSError:
            log.warning("could not write %s", path.name, exc_info=True)
        await asyncio.sleep(every)


# --------------------------------------------------------------------------- the command


def hide_addresses(text: str) -> str:
    return _IPV4.sub("<ip>", _URL.sub("<url>", text))


def _age(iso: str | None, now: datetime) -> str:
    if not iso:
        return "never"
    seconds = max(0, int((now - datetime.fromisoformat(iso)).total_seconds()))
    if seconds < 120:
        return f"{seconds}s ago"
    if seconds < 7200:
        return f"{seconds // 60}m ago"
    if seconds < 172_800:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86_400}d ago"


def report(data: dict[str, Any], now: datetime, *, show_addresses: bool = False) -> list[str]:
    """One line per provider: status, last success, failures, resources, the last error."""
    lines = []
    for p in data["providers"]:
        status = p["status"]
        if p.get("circuit_open_until"):
            status += " (circuit open)"
        line = (
            f"{p['name']:<18} {status:<22} last ok {_age(p.get('last_success'), now):<10} "
            f"{p.get('resource_count', 0):>4} resources"
        )
        if p.get("consecutive_failures"):
            line += f"  {p['consecutive_failures']} failures in a row"
        error = p.get("last_error")
        if error:
            line += "\n" + " " * 19 + (error if show_addresses else hide_addresses(error))
        lines.append(line)
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hud.providers.status", description=__doc__)
    parser.add_argument("--name", help="only this provider")
    parser.add_argument("--json", action="store_true", help="the snapshot as JSON")
    parser.add_argument("--show-addresses", action="store_true", help="keep URLs and IPs")
    args = parser.parse_args(argv)
    path = HudEnv().data_dir / SNAPSHOT_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"no provider snapshot at {path}: is HUD running?", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    written = datetime.fromisoformat(data["written_at"])
    if args.name:
        data["providers"] = [p for p in data["providers"] if p["name"] == args.name]
        if not data["providers"]:
            print(f"no provider named {args.name!r}", file=sys.stderr)
            return 3
    if args.json:
        if not args.show_addresses:
            for p in data["providers"]:
                if p.get("last_error"):
                    p["last_error"] = hide_addresses(p["last_error"])
        print(json.dumps(data, indent=1))
    else:
        print(f"providers as of {_age(data['written_at'], now)} ({written:%Y-%m-%d %H:%M:%S} UTC)")
        print("\n".join(report(data, now, show_addresses=args.show_addresses)))
    if (now - written).total_seconds() > STALE_SECONDS:
        print(f"STALE: older than {STALE_SECONDS}s — HUD may be stopped", file=sys.stderr)
        return 2
    return 0 if all(p["status"] == "ok" for p in data["providers"]) else 1


if __name__ == "__main__":
    sys.exit(main())
