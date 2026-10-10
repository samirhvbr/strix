"""Export bounded offline accounting; no target, provider or authority requests."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from strix.core.evidence_ledger import EvidenceError
from strix.core.history_export import export_history


def run_history_export(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="strix history", description=__doc__)
    parser.add_argument("run", type=Path, help="Existing, stopped controlled assessment run")
    args = parser.parse_args(argv)
    try:
        result = json.dumps(export_history(args.run), sort_keys=True, allow_nan=False)
        if len(result.encode()) > 2 * 1024 * 1024:
            return _unavailable()
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, EvidenceError):
        return _unavailable()
    sys.stdout.write(result + "\n")
    return 0


def _unavailable() -> int:
    sys.stderr.write('{"error":"assessment_history_unavailable"}\n')
    return 1
