"""Catalog of individually trackable "tests".

A "test" is a specialist agent the root agent decides, in real time, to spawn
via ``create_agent``. Decision D6 (``.continue/pentest/PENTEST-00``): the
decomposition stays emergent -- the LLM still decides when and what to spawn
-- this module only gives each spawned specialist a stable, catalogued
identity, so a UI can show a checklist that grows live instead of a fixed
roster the engine imposes.

``TestCatalog`` is a sibling of ``AgentCoordinator``, not part of it: the
coordinator calls an ``on_status_change`` callback (see ``agents.py``) after
every status transition, and this module is one possible listener. That
keeps the coordinator ignorant of test-catalog concerns -- it only knows
"someone wants to be notified".
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from strix.report.coverage import VULN_CLASSES


if TYPE_CHECKING:
    from collections.abc import Callable


logger = logging.getLogger(__name__)

Status = Literal["pending", "running", "completed", "stopped", "crashed", "failed"]

# The subset of AgentCoordinator's own Status values a TestUnit tracks.
# "waiting" and "budget_paused" are coordinator-only states (a test being
# paused or waiting on a peer is not a different *test* status -- Spec 03
# handles pause as its own concern) so a transition into either is ignored
# here rather than forced into an invalid TestUnit status.
_RECOGNIZED_STATUSES: frozenset[str] = frozenset(
    {"running", "completed", "stopped", "crashed", "failed"}
)
_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "stopped", "crashed", "failed"})

# Best-effort target/endpoint extraction from free-text task descriptions.
# Never required (TestUnit.surface may stay None); a false negative here
# breaks nothing.
_SURFACE_RE = re.compile(
    r"https?://[^\s'\"<>]+|(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?:/[^\s'\"<>]*)?",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _skill_leaf(skill: str) -> str:
    """Bare skill name from a possibly ``category/name`` string, lowercased.

    Mirrors ``strix.report.coverage._skill_leaf`` (kept local rather than
    imported: that helper is private, and this is the only place outside
    ``coverage.py`` that needs it).
    """
    return skill.rsplit("/", maxsplit=1)[-1].strip().lower()


def _infer_vuln_class(skills: list[str]) -> str | None:
    """The first skill that names a known vulnerability class, or ``None``.

    ``create_agent`` tells the root agent to aim for 1-3 related skills with
    the most focused one first (see its docstring), so the first match is
    the test's primary class. A skill with no match is not an error -- the
    test is still catalogued, just without a class to group it by.
    """
    for skill in skills:
        leaf = _skill_leaf(skill)
        if leaf in VULN_CLASSES:
            return leaf
    return None


def _infer_surface(task: str) -> str | None:
    """Best-effort URL/domain extracted from the task text."""
    match = _SURFACE_RE.search(task or "")
    return match.group(0) if match else None


@dataclass(slots=True)
class TestUnit:
    # Tells pytest's default Test* collection not to treat this domain class
    # (named for the domain concept -- a pentest "test" -- not for the test
    # suite) as a test class to instantiate. ClassVar is excluded from
    # dataclass fields/slots.
    __test__: ClassVar[bool] = False

    test_id: str
    agent_id: str
    name: str
    vuln_class: str | None
    surface: str | None
    source: Literal["emergent", "seeded"]
    status: Status
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    parent_test_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TestCatalog:
    """Owns the catalog's state; a sibling of ``AgentCoordinator``, not part of it.

    Every public mutator persists through a synchronous atomic write
    (tempfile + rename) -- plain blocking I/O, the same thing
    ``AgentCoordinator._maybe_snapshot`` does even though it is declared
    ``async def`` (Python file I/O is blocking either way; the run directory
    is local disk and the file is small). The blocking write matters here
    specifically: ``mark_status`` is called synchronously from inside
    ``AgentCoordinator._set_status_locked``, which already runs under the
    coordinator's ``asyncio.Lock`` and cannot ``await``.
    """

    __test__: ClassVar[bool] = False  # same reason as TestUnit above

    def __init__(self) -> None:
        self._tests: dict[str, TestUnit] = {}  # agent_id -> TestUnit
        self._snapshot_path: Path | None = None
        self._on_change: Callable[[TestUnit], None] | None = None

    def get(self, agent_id: str) -> TestUnit | None:
        return self._tests.get(agent_id)

    def set_change_callback(self, callback: Callable[[TestUnit], None] | None) -> None:
        """Observe registration/status changes and hydrate existing units on resume."""
        self._on_change = callback
        if callback is not None:
            for unit in self._tests.values():
                callback(unit)

    def _notify(self, unit: TestUnit) -> None:
        if self._on_change is not None:
            self._on_change(unit)

    def set_snapshot_path(self, path: Path) -> None:
        self._snapshot_path = path

    def load(self, path: Path | None = None) -> None:
        """Load an existing ``test_catalog.json`` into memory (the resume path).

        An agent that already existed before this process started is never
        re-registered through ``create_agent`` -- ``respawn_subagents``
        starts its runner directly, bypassing ``register`` entirely (it is
        already in ``AgentCoordinator``'s restored snapshot). Without this
        load, every entry from a previous run would be missing after
        ``strix --resume``, even though the agents themselves came back.
        """
        source = path or self._snapshot_path
        if source is None or not source.exists():
            return
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("failed to load test catalog from %s", source)
            return
        tests = raw.get("tests", {})
        if not isinstance(tests, dict):
            return
        for agent_id, data in tests.items():
            if not isinstance(data, dict):
                continue
            try:
                unit = TestUnit(**data)
            except TypeError:
                logger.warning("skipping malformed test_catalog entry for %s", agent_id)
                continue
            self._tests[str(agent_id)] = unit

    def register(
        self,
        *,
        agent_id: str,
        name: str,
        skills: list[str] | None = None,
        task: str = "",
        parent_agent_id: str | None = None,
    ) -> TestUnit:
        """Catalog a newly spawned specialist agent as a test.

        Only called for agents created via ``create_agent`` (``parent_id is
        not None`` at the call site) -- the root agent is the tree's trunk,
        not a test (D6/Spec-01 §2.4), and is never passed here.
        """
        now = _now()
        parent_test_id = None
        if parent_agent_id is not None:
            parent = self._tests.get(parent_agent_id)
            if parent is not None:
                parent_test_id = parent.test_id
        unit = TestUnit(
            test_id=f"test_{uuid.uuid4().hex[:8]}",
            agent_id=agent_id,
            name=name,
            vuln_class=_infer_vuln_class(skills or []),
            surface=_infer_surface(task),
            source="emergent",
            status="running",
            created_at=now,
            started_at=now,
        )
        unit.parent_test_id = parent_test_id
        self._tests[agent_id] = unit
        logger.info(
            "test_catalog.register %s (%s) agent=%s vuln_class=%s",
            unit.test_id,
            name,
            agent_id,
            unit.vuln_class or "-",
        )
        self._write()
        self._notify(unit)
        return unit

    def mark_status(self, agent_id: str, status: str) -> None:
        """Observe a status transition reported by ``AgentCoordinator``.

        A no-op for an agent this catalog never registered (the root agent,
        in particular) and for a coordinator-only status this catalog does
        not track (``waiting``, ``budget_paused``) -- see
        ``_RECOGNIZED_STATUSES``.
        """
        if status not in _RECOGNIZED_STATUSES:
            return
        unit = self._tests.get(agent_id)
        if unit is None:
            return
        unit.status = status  # type: ignore[assignment]
        now = _now()
        if status == "running":
            if unit.started_at is None:
                unit.started_at = now
            unit.ended_at = None
        if status in _TERMINAL_STATUSES and unit.ended_at is None:
            unit.ended_at = now
        self._write()
        self._notify(unit)

    def snapshot(self) -> dict[str, Any]:
        return {"tests": {agent_id: unit.to_dict() for agent_id, unit in self._tests.items()}}

    def _write(self) -> None:
        path = self._snapshot_path
        if path is None:
            return
        try:
            payload = json.dumps(self.snapshot(), ensure_ascii=False, default=str)
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(path.parent),
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp.write(payload)
                tmp_path = Path(tmp.name)
            tmp_path.replace(path)
        except Exception:
            logger.exception("test catalog snapshot to %s failed", path)


def catalog_from_context(ctx: dict[str, Any]) -> TestCatalog | None:
    catalog = ctx.get("test_catalog")
    return catalog if isinstance(catalog, TestCatalog) else None
