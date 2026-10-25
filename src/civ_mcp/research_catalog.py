"""Search verified cached research rules and their unlock/effect associations."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

FILTERS = {
    "food": "食物", "science": "科学", "production": "生产力", "culture": "文化值",
    "gold": "金币", "faith": "信仰值", "housing": "住房", "unit": "单位",
    "improvement": "改良设施", "wonder": "奇观",
}
ALIASES = {**{v: k for k, v in FILTERS.items()}, "文化": "culture", "信仰": "faith", "科研": "science"}
YIELDS = {"YIELD_" + k.upper(): k for k in FILTERS if k not in {"housing", "unit", "improvement", "wonder"}}


def search(snapshot, cards, query="", filters=None, match="any", limit=20, offset=0):
    filters = [ALIASES.get(v, v) for v in (filters or [])]
    if any(v not in FILTERS for v in filters) or match not in {"any", "all"}:
        raise ValueError("Unknown research filter or match; use " + ", ".join(FILTERS))
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError("limit must be 1..100; offset must be nonnegative")
    tables = snapshot["tables"]
    yield_links = defaultdict(list)
    for table, key in [("Building_YieldChanges", "BuildingType"), ("Building_YieldChangesBonusWithPower", "BuildingType"),
                       ("Building_CitizenYieldChanges", "BuildingType"), ("Improvement_YieldChanges", "ImprovementType"),
                       ("District_CitizenYieldChanges", "DistrictType")]:
        for index, row in enumerate(tables.get(table, [])):
            if row.get("YieldType") in YIELDS and (row.get("YieldChange") or 0) > 0:
                yield_links[row.get(key)].append((YIELDS[row["YieldType"]], table, index))
    adjacency = {r.get("ID"): r for r in tables.get("Adjacency_YieldChanges", [])}
    for index, row in enumerate(tables.get("District_Adjacencies", [])):
        value = adjacency.get(row.get("YieldChangeId"), {})
        if value.get("YieldType") in YIELDS and (value.get("YieldChange") or 0) > 0:
            yield_links[row.get("DistrictType")].append((YIELDS[value["YieldType"]], "District_Adjacencies", index))
    arguments = defaultdict(dict)
    for row in tables.get("ModifierArguments", []):
        arguments[row.get("ModifierId")][row.get("Name")] = row.get("Value")
    modifiers = {r.get("ModifierId"): r for r in tables.get("Modifiers", [])}
    modifier_links = defaultdict(list)
    for table, key in [("BuildingModifiers", "BuildingType"), ("PolicyModifiers", "PolicyType"),
                       ("ImprovementModifiers", "ImprovementType"), ("TraitModifiers", "TraitType")]:
        for index, row in enumerate(tables.get(table, [])):
            modifier_links[row.get(key)].append((row.get("ModifierId"), table, index))

    def effects(entity, seen=None):
        seen = set() if seen is None else seen
        out = []
        for modifier, table, index in modifier_links[entity]:
            if modifier in seen:
                continue
            seen.add(modifier)
            args = arguments[modifier]
            try:
                positive = float(args.get("Amount", 1)) > 0
            except (ValueError, TypeError):
                positive = False
            effect = str(modifiers.get(modifier, {}).get("ModifierType", ""))
            if positive and "COST" not in effect:
                if args.get("YieldType") in YIELDS:
                    out.append((YIELDS[args["YieldType"]], table, index))
                if "HOUSING" in str(modifiers.get(modifier, {}).get("ModifierType", "")):
                    out.append(("housing", table, index))
                if "ADJUST" in effect:
                    for yield_type, tag in YIELDS.items():
                        if re.search(r"(?:^|_)" + tag.upper() + r"(?:_|$)", effect):
                            out.append((tag, table, index))
            nested = args.get("ModifierId")
            if nested and nested not in seen:
                modifier_links[modifier] = [(nested, table, index)]
                out.extend(effects(modifier, seen))
        return out

    rows = []
    needle = query.casefold().strip()
    for card in sorted(cards, key=lambda c: c["id"]):
        evidence = defaultdict(list)
        unlocks = card["rule"].get("unlocks", [])
        for item in unlocks:
            entity, table = item["id"], item["entity_type"]
            facts = item.get("conditions", {})
            reference = item.get("source_ref", {})
            if table == "Units": evidence["unit"].append(reference)
            if table == "Improvements": evidence["improvement"].append(reference)
            if table == "Buildings" and facts.get("IsWonder"): evidence["wonder"].append(reference)
            if (facts.get("Housing") or 0) > 0: evidence["housing"].append(reference)
            for tag, source, index in [*yield_links[entity], *effects(entity)]:
                evidence[tag].append({"table": source, "row": index, "via": entity})
        # Improvements can gain yields from later research without being newly unlocked.
        for index, row in enumerate(tables.get("Improvement_YieldChanges", [])):
            if card["id"] in (row.get("PrereqTech"), row.get("PrereqCivic")) and row.get("YieldType") in YIELDS and (row.get("YieldChange") or 0) > 0:
                evidence[YIELDS[row["YieldType"]]].append({"table": "Improvement_YieldChanges", "row": index})
        haystack = " ".join([card["id"], *card["names"].values(), card["summary"].get("description", ""),
                             *(name for u in unlocks for name in u.get("names", {}).values())]).casefold()
        if needle and needle not in haystack:
            continue
        if filters and not (all(v in evidence for v in filters) if match == "all" else any(v in evidence for v in filters)):
            continue
        rows.append({"id": card["id"], "names": card["names"], "description": card["summary"].get("description", ""),
                     "tags": sorted(evidence), "match_evidence": dict(evidence),
                     "unlocks": [{k: v for k, v in u.items() if k != "conditions"} for u in unlocks],
                     "prerequisites": card["rule"].get("prerequisites", []),
                     "facts": card["rule"].get("facts", {})})
    return {"status": "found" if rows else "not_found", "total": len(rows), "offset": offset, "limit": limit,
            "truncated": offset + limit < len(rows), "results": rows[offset:offset+limit],
            "filters": filters, "match": match,
            "filter_scope": "Direct unlocks and captured positive yield/housing modifier associations; conditions and civilization ownership are not evaluated"}


def query(root: Path, kind: str, **options):
    if kind not in {"technology", "civic"}:
        raise ValueError("kind must be technology or civic")
    manifest_bytes = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    topic = "technologies" if kind == "technology" else "civics"
    snapshot = (root / "sources/snapshot.json").read_bytes()
    data = (root / f"rules/{topic}.json").read_bytes()
    if hashlib.sha256(snapshot).hexdigest() != manifest["snapshot_sha256"] or hashlib.sha256(data).hexdigest() != manifest["files"][f"rules/{topic}.json"]:
        raise ValueError("Knowledge snapshot/rule fingerprint mismatch")
    decoded = json.loads(snapshot)
    if decoded.get("profile") != manifest.get("profile"):
        raise ValueError("Knowledge profile differs from source snapshot")
    result = search(decoded, json.loads(data)["cards"], **options)
    if (root / "manifest.json").read_bytes() != manifest_bytes:
        raise ValueError("Knowledge manifest changed during search")
    return result | {"profile": manifest["profile"], "snapshot_sha256": manifest["snapshot_sha256"],
                     "current_session_verified": False, "kind": kind}
