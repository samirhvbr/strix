"""Read-only activity window entry point; no scan setup, authorization call or provider access."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from strix.core.activity import DEFAULT_LIMIT, MAX_LIMIT, read_activity


def run_activity(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="strix activity", description=__doc__)
    parser.add_argument("run", type=Path, help="Existing run directory")
    parser.add_argument(
        "--after",
        type=int,
        default=0,
        help="Only rows after this id (last_id of the previous answer); 0 for the newest window",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help=f"Rows per answer, 1 to {MAX_LIMIT}"
    )
    args = parser.parse_args(argv)
    try:
        window = read_activity(args.run, after=args.after, limit=args.limit)
        encoded = json.dumps(window, sort_keys=True, ensure_ascii=False)
        if len(encoded.encode()) > 512 * 1024:
            return _unavailable()
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        # Neither a rejected value nor a private path belongs on this boundary.
        return _unavailable()
    sys.stdout.write(encoded + "\n")
    return 0


def _unavailable() -> int:
    sys.stderr.write('{"error":"activity_unavailable"}\n')
    return 1
