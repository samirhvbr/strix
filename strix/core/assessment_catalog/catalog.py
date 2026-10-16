"""The 171-check assessment catalog and its curated selection (PENTEST-13, ENG-13).

Pure data. Nothing here creates, orders or limits agents: the agent still decides how to test, and
decision D6 stays as it was. ``items.yaml`` carries every check from section 5 of the product
document; ``selection.yaml`` says which of them are essential, which facts make them applicable and
how much of each the runtime adapters really exercise.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from strix.report.coverage import VULN_CLASSES


#: Adapter name -> the fact an approved case establishes by declaring it.
ADAPTER_FACTS = {
    "http.private-read": "case.authorization",
    "http.single-credit": "case.business",
    "openssl.tls": "transport.tls",
    "ssh-audit": "transport.ssh",
    "http.web-hygiene": "case.web_hygiene",
}
#: The closed vocabulary ``applies_when`` may use.
FACTS = frozenset({*ADAPTER_FACTS.values(), "operation.http"})
WEIGHTS = frozenset({"essential", "consultative"})
SCOPES = frozenset({"full", "partial"})
#: http.web-hygiene is one case producing four independent sub-verdicts (PENTEST-15/PT-23); each
#: essential item that closes with it must name exactly which one it reads.
WEB_HYGIENE_CHECKS = frozenset({"redirect", "cors", "cookies", "disclosure"})


class CatalogError(ValueError):
    """The catalog or its selection is malformed."""


@dataclass(frozen=True, slots=True)
class CatalogItem:
    id: str
    family_id: str
    family_title: str
    text: str
    weight: str = "consultative"
    label: str | None = None
    coverage_class: str | None = None
    applies_when: tuple[str, ...] = ()
    adapter: str | None = None
    scope: str | None = None
    limits: str | None = None
    limits_pt: str | None = None
    check: str | None = None


@dataclass(frozen=True, slots=True)
class Catalog:
    catalog_version: int
    selection_version: int
    essential_limit: int
    families: int
    items: dict[str, CatalogItem]
    sha256: str

    @property
    def essential(self) -> list[CatalogItem]:
        return [item for item in self.items.values() if item.weight == "essential"]


def _read(name: str) -> dict[str, Any]:
    text = (resources.files(__package__) / name).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise CatalogError(f"{name} must hold a mapping")
    return data


def _int(data: dict[str, Any], key: str, name: str) -> int:
    value = data.get(key)
    if type(value) is not int or value < 1:
        raise CatalogError(f"{name}: {key} must be a positive integer")
    return value


def _families(raw: dict[str, Any]) -> dict[str, CatalogItem]:
    items: dict[str, CatalogItem] = {}
    families = raw.get("families")
    if not isinstance(families, list) or not families:
        raise CatalogError("items.yaml: families must be a non-empty list")
    for family in families:
        family_id, title = str(family.get("id", "")), str(family.get("title", ""))
        if not family_id or not title or not family.get("items"):
            raise CatalogError(f"items.yaml: family {family_id or '?'} is incomplete")
        for entry in family["items"]:
            item_id, text = str(entry.get("id", "")), str(entry.get("text", ""))
            if not item_id.startswith(family_id + ".") or not text or item_id in items:
                raise CatalogError(f"items.yaml: invalid or repeated item {item_id or '?'}")
            items[item_id] = CatalogItem(item_id, family_id, title, text)
    return items


def _curate(item: CatalogItem, entry: dict[str, Any]) -> CatalogItem:
    weight = entry.get("weight")
    if weight not in WEIGHTS:
        raise CatalogError(f"selection.yaml: {item.id} has an unknown weight")
    label = entry.get("label")
    if not isinstance(label, str) or not label:
        raise CatalogError(f"selection.yaml: {item.id} needs an English label")
    coverage_class = entry.get("coverage_class")
    if coverage_class is not None and coverage_class not in VULN_CLASSES:
        raise CatalogError(f"selection.yaml: {item.id} names an unknown coverage class")
    facts = entry.get("applies_when")
    if not isinstance(facts, list) or not facts or not set(facts) <= FACTS:
        raise CatalogError(f"selection.yaml: {item.id} needs facts from the closed vocabulary")
    closes = entry.get("closes_with")
    adapter = scope = limits = limits_pt = check = None
    if weight == "essential":
        if not isinstance(closes, dict):
            raise CatalogError(f"selection.yaml: essential {item.id} needs closes_with")
        adapter, scope, limits = closes.get("adapter"), closes.get("scope"), closes.get("limits")
        limits_pt = closes.get("limits_pt")
        if adapter not in ADAPTER_FACTS or scope not in SCOPES or not limits or not limits_pt:
            raise CatalogError(f"selection.yaml: {item.id} closes_with is incomplete")
        if facts != [ADAPTER_FACTS[adapter]]:
            raise CatalogError(f"selection.yaml: {item.id} facts must be its adapter's fact")
        # http.web-hygiene is one case with four independent sub-verdicts; every essential item
        # that closes with it must name exactly which one, so two items never silently share a
        # verdict the way the other, single-outcome adapters do.
        check = closes.get("check")
        if adapter == "http.web-hygiene":
            if check not in WEB_HYGIENE_CHECKS:
                raise CatalogError(f"selection.yaml: {item.id} needs a known web-hygiene check")
        elif check is not None:
            raise CatalogError(f"selection.yaml: {item.id} adapter does not use check")
    elif closes is not None:
        raise CatalogError(f"selection.yaml: consultative {item.id} cannot close with an adapter")
    return CatalogItem(
        item.id,
        item.family_id,
        item.family_title,
        item.text,
        weight,
        label,
        coverage_class,
        tuple(facts),
        adapter,
        scope,
        " ".join(str(limits).split()) if limits else None,
        " ".join(str(limits_pt).split()) if limits_pt else None,
        check,
    )


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    raw_items, raw_selection = _read("items.yaml"), _read("selection.yaml")
    items = _families(raw_items)
    selected = raw_selection.get("items")
    if not isinstance(selected, dict):
        raise CatalogError("selection.yaml: items must be a mapping")
    for item_id, entry in selected.items():
        if item_id not in items or not isinstance(entry, dict):
            raise CatalogError(f"selection.yaml: {item_id} is not a catalog item")
        items[item_id] = _curate(items[item_id], entry)
    limit = _int(raw_selection, "essential_limit", "selection.yaml")
    catalog = Catalog(
        catalog_version=_int(raw_items, "catalog_version", "items.yaml"),
        selection_version=_int(raw_selection, "selection_version", "selection.yaml"),
        essential_limit=limit,
        families=len(raw_items["families"]),
        items=items,
        sha256=hashlib.sha256(
            json.dumps([raw_items, raw_selection], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )
    if len(catalog.essential) > limit:
        raise CatalogError(f"selection.yaml: more than {limit} essential items")
    return catalog
