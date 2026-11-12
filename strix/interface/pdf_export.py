"""Render one run's PDF report offline, into a file the caller names; no server, no network."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

from strix.core.paths import run_record_path
from strix.interface.viewer.report_pdf import generate_report_pdf


# The WEB refuses a PDF above this size and a report that big is not a report, so the
# bound is the same on both sides of the product.
MAX_PDF_BYTES = 20 * 1024 * 1024


def run_pdf_export(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="strix report-pdf", description=__doc__)
    parser.add_argument("run", type=Path, help="Existing run directory")
    parser.add_argument(
        "--output", type=Path, required=True, help="File to create; an existing one is refused"
    )
    args = parser.parse_args(argv)
    try:
        # A directory with no run record would still render a PDF (an empty one), which is
        # what a wrong path must not look like.
        if not run_record_path(args.run).is_file():
            return _unavailable()
        pdf = generate_report_pdf(args.run)
        if not pdf.startswith(b"%PDF-") or len(pdf) > MAX_PDF_BYTES:
            return _unavailable()
        _write_new_private_file(args.output, pdf)
    except Exception:  # noqa: BLE001 - this is the process boundary: a fixed answer, never a path or a trace
        return _unavailable()
    sys.stdout.write(json.dumps({"bytes": len(pdf)}) + "\n")
    return 0


def _write_new_private_file(path: Path, data: bytes) -> None:
    """Create ``path`` (owner-only) with ``data``, or leave nothing behind.

    The file is made exclusively, so a name that already exists is an error and a symlink
    planted there is never followed. A write that fails half way removes what it made.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()
        raise


def _unavailable() -> int:
    sys.stderr.write('{"error":"report_pdf_unavailable"}\n')
    return 1
