"""Run one authorized private-read retest without scan, model or Docker setup."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from strix.core.assessment_retest import retest_assessment
from strix.core.evidence_ledger import EvidenceError
from strix.core.run_lease import RunAlreadyOwnedError


def run_retest(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="strix retest", description=__doc__)
    parser.add_argument("run", type=Path, help="Existing controlled assessment run directory")
    parser.add_argument("--case", required=True, dest="case_ref", help="Approved case reference")
    parser.add_argument("--baseline", required=True, help="Verified case result reference")
    parser.add_argument("--web-authorization", required=True, type=Path)
    parser.add_argument("--identity-credentials", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(
            retest_assessment(
                args.run,
                case_ref=args.case_ref,
                baseline_ref=args.baseline,
                authorization_path=args.web_authorization,
                credentials_path=args.identity_credentials,
            )
        )
    except (EvidenceError, RunAlreadyOwnedError, OSError, ValueError, TypeError, KeyError):
        # Never forward private paths, rejected values, credentials or target content.
        sys.stderr.write('{"error":"assessment_retest_unavailable"}\n')
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0
