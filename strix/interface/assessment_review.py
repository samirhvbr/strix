"""Read-only review entry point; no scan setup, authorization call or provider access."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from strix.core.assessment_review import review_assessment
from strix.core.evidence_ledger import EvidenceError


def run_review(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="strix review", description=__doc__)
    parser.add_argument("run", type=Path, help="Existing controlled assessment run directory")
    args = parser.parse_args(argv)
    try:
        report = review_assessment(args.run)
        encoded = json.dumps(report, sort_keys=True)
        if len(encoded.encode()) > 2 * 1024 * 1024:
            return _unavailable()
    except (EvidenceError, OSError, ValueError, TypeError, KeyError):
        # Neither rejected local values nor private artifact paths belong on this boundary.
        return _unavailable()
    sys.stdout.write(encoded + "\n")
    return 0


def _unavailable() -> int:
    sys.stderr.write('{"error":"assessment_review_unavailable"}\n')
    return 1
