"""Export report content offline; transmitting it requires separate operator consent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from strix.core.evidence_ledger import EvidenceError
from strix.core.report_export import export_report


def run_report_export(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="strix report-export", description=__doc__)
    parser.add_argument("run", type=Path, help="Verified stopped assessment")
    args = parser.parse_args(argv)
    try:
        result = json.dumps(export_report(args.run), sort_keys=True, allow_nan=False)
    except (OSError, ValueError, TypeError, KeyError, EvidenceError):
        sys.stderr.write('{"error":"assessment_report_unavailable"}\n')
        return 1
    sys.stdout.write(result + "\n")
    return 0
