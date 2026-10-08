"""The SHVIA release identity, separate from the upstream package version."""

from __future__ import annotations

import re
from pathlib import Path


_VERSION_PATHS = (
    Path(__file__).parent / ".fork-version",  # Wheels and PyInstaller bundles.
    Path(__file__).parent.parent / ".fork-version",  # Source checkouts.
)


def fork_version() -> str | None:
    """Return the bundled fork version, or unknown; never substitute upstream's."""
    for path in _VERSION_PATHS:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if re.fullmatch(r"\d+\.\d+\.\d+", value):
            return value
    return None
