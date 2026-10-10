"""Durable, idempotent automatic pause from the catalog's single event stream."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar


if TYPE_CHECKING:
    from strix.core.agents import AgentCoordinator
    from strix.core.test_catalog import TestUnit


def validate_interval(value: int | None) -> None:
    if value is not None and (type(value) is not int or value <= 0):
        raise ValueError("pause_every_n_tests must be a positive integer or None")


class TestPauseController:
    """Count each catalog test once, including failed/stopped terminal outcomes.

    A pending pause is persisted independently of agents.json, so process death
    cannot erase it before the coordinator finishes saving its own snapshot.
    Catalog hydration repairs the opposite crash gap (catalog saved first).
    """

    __test__: ClassVar[bool] = False

    def __init__(
        self, coordinator: AgentCoordinator, path: Path, *, pause_every_n_tests: int | None
    ) -> None:
        validate_interval(pause_every_n_tests)
        self.coordinator = coordinator
        self.path = path
        self.interval = pause_every_n_tests
        self.completed_since_resume = 0
        self.seen: set[str] = set()
        self.paused = False
        if path.exists():
            self._load()
        elif self.interval is not None:
            self._write(count=0, seen=set(), paused=False)
        elif "test" in coordinator.pause_reasons:
            raise ValueError("missing state for an active test pause")
        if self.paused:
            self.coordinator.request_pause("test")
        self.coordinator.set_pause_release_callback("test", self._acknowledge)

    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        saved_interval = data["pause_every_n_tests"]
        validate_interval(saved_interval)
        if saved_interval is None or data["version"] != 1:
            raise ValueError("invalid saved interval/version")
        if self.interval is not None and self.interval != saved_interval:
            raise ValueError("pause interval cannot change on resume")
        count, seen, paused = data["completed_since_resume"], data["seen"], data["paused"]
        if (
            type(count) is not int
            or count < 0
            or not isinstance(seen, list)
            or any(not isinstance(item, str) or not item for item in seen)
            or len(set(seen)) != len(seen)
            or count > len(seen)
            or type(paused) is not bool
            or paused != (count >= saved_interval)
        ):
            raise ValueError("invalid saved pause state")
        self.interval = saved_interval
        self.completed_since_resume = count
        self.seen = set(seen)
        self.paused = paused

    def observe(self, unit: TestUnit) -> None:
        if (
            self.interval is None
            or unit.status not in {"completed", "stopped", "crashed", "failed"}
            or unit.test_id in self.seen
        ):
            return
        self.seen.add(unit.test_id)
        self.completed_since_resume += 1
        self.paused = self.completed_since_resume >= self.interval
        if self.paused:
            self.coordinator.request_pause("test")
        try:
            self._write(count=self.completed_since_resume, seen=self.seen, paused=self.paused)
        except OSError:
            # The coordinator logs callback errors. Keep the admission gate closed
            # even if persistence failed before reaching the configured threshold.
            self.coordinator.request_pause("test")
            raise

    def _acknowledge(self) -> None:
        if self.interval is None:
            return
        self._write(count=0, seen=self.seen, paused=False)
        self.completed_since_resume = 0
        self.paused = False

    def _write(self, *, count: int, seen: set[str], paused: bool) -> None:
        payload = json.dumps(
            {
                "version": 1,
                "pause_every_n_tests": self.interval,
                "completed_since_resume": count,
                "seen": sorted(seen),
                "paused": paused,
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as file:
                temporary = Path(file.name)
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
