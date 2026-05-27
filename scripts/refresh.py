#!/usr/bin/env python3
"""Trigger and poll the daily refresh.

Refresh is async on the backend (BackgroundTasks). We poll until state is
'done' or 'error', or until --max-wait expires. The agent doesn't need to
block on this — but the IM user expects "刷一下" to actually finish before
they ask follow-up questions, so we wait by default.

Usage:
    uv run scripts/refresh.py [--no-wait] [--max-wait 60]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_json, get_json, post_json  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Trigger NAV/FX refresh")
    parser.add_argument("--no-wait", action="store_true",
                        help="kick off and return immediately")
    parser.add_argument("--max-wait", type=int, default=60,
                        help="max seconds to poll (default 60)")
    args = parser.parse_args()

    initial = post_json("/api/refresh/run", {})
    if args.no_wait:
        emit_json({"started": True, "status": initial})
        return

    deadline = time.time() + args.max_wait
    last = initial
    while time.time() < deadline:
        time.sleep(1.5)
        last = get_json("/api/refresh/status")
        if last.get("state") in ("done", "error", "idle"):
            break

    emit_json({
        "finished": last.get("state") in ("done", "idle"),
        "status": last,
    })


if __name__ == "__main__":
    main()
