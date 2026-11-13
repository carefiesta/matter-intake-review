"""Flag missing checklist items, duplicates, and conflicts.

A conflict record has no winning value. A duplicate keeps every source
and points at one display instance so the report does not list it twice.
"""

from __future__ import annotations

from intake.constants import (
    CHECKLIST,
    FLAG_CONFLICT,
    FLAG_DUPLICATE,
    FLAG_INCOMPLETE,
    FLAG_MISSING,
    MULTI_FACT_TYPES,
    UNIQUE_FACT_TYPES,
)


def _signature(flag_type: str, key: str, members: list[dict]) -> str:
    parts = []
    for member in members:
        parts.append(
            "|".join(
                [
                    str(member.get("fact_id") or ""),
                    str(member.get("normalized_value") or ""),
                    str(member.get("document_id") or ""),
                    str(member.get("locator") or ""),
                ]
            )
        )
    parts.sort()
    return flag_type + "||" + key + "||" + "||".join(parts)


def _member(fact: dict) -> dict:
    return {
        "fact_id": fact["fact_id"],
        "value": fact["value"],
        "normalized_value": fact["normalized_value"],
        "document_id": fact["document_id"],
        "locator": fact["locator"],
        "excerpt": fact["excerpt"],
    }


def build_flags(facts: list[dict], documents: list[dict], problems: list[dict]) -> list[dict]:
    """Build the current flag set from active facts and extraction problems.

    `problems` entries look like {"document_id", "filename", "reason"}.
    """
    drafts: list[dict] = []
    by_type: dict[str, list[dict]] = {}
    for fact in facts:
        by_type.setdefault(fact["fact_type"], []).append(fact)

    for fact_type in list(UNIQUE_FACT_TYPES) + list(MULTI_FACT_TYPES):
        group = by_type.get(fact_type, [])
        if len(group) < 2 and fact_type in MULTI_FACT_TYPES:
            continue
        buckets: dict[str, list[dict]] = {}
        for fact in group:
            buckets.setdefault(fact["normalized_value"], []).append(fact)
        if fact_type in UNIQUE_FACT_TYPES and len(buckets) >= 2:
            members = [_member(fact) for fact in group]
            drafts.append(
                {
                    "flag_type": FLAG_CONFLICT,
                    "fact_type": fact_type,
                    "checklist_key": fact_type,
                    "blocks_ready": 1,
                    "gap_reason": None,
                    "display_fact_id": None,
                    "document_id": None,
                    "members": members,
                    "signature": _signature(FLAG_CONFLICT, fact_type, members),
                }
            )
            continue
        for normalized, items in buckets.items():
            if len(items) < 2:
                continue
            if fact_type == "document_date":
                # Each file can carry its own date. Same words on two files
                # are not treated as a duplicate of one matter-level fact.
                continue
            ordered = sorted(items, key=lambda row: (row["version"], row["document_id"], row["fact_id"]))
            members = [_member(fact) for fact in ordered]
            drafts.append(
                {
                    "flag_type": FLAG_DUPLICATE,
                    "fact_type": fact_type,
                    "checklist_key": fact_type,
                    "blocks_ready": 0,
                    "gap_reason": None,
                    "display_fact_id": ordered[0]["fact_id"],
                    "document_id": None,
                    "members": members,
                    "signature": _signature(FLAG_DUPLICATE, fact_type + ":" + normalized, members),
                }
            )

    present = set(by_type)
    current_docs = [doc for doc in documents if doc.get("is_current")]
    readable_docs = [doc for doc in current_docs if doc.get("status") == "extracted"]

    for item in CHECKLIST:
        if item["key"] == "document_dates":
            missing_docs = []
            for doc in readable_docs:
                has_date = any(
                    fact["fact_type"] == "document_date" and fact["document_id"] == doc["document_id"]
                    for fact in facts
                )
                if not has_date:
                    missing_docs.append(doc["filename"])
            if not readable_docs or missing_docs:
                reason = (
                    "checklist gap: document date is absent for "
                    + ", ".join(missing_docs or ["the readable documents"])
                )
                members: list[dict] = []
                drafts.append(
                    {
                        "flag_type": FLAG_MISSING,
                        "fact_type": "document_date",
                        "checklist_key": item["key"],
                        "blocks_ready": 0,
                        "gap_reason": reason,
                        "display_fact_id": None,
                        "document_id": None,
                        "members": members,
                        "signature": _signature(FLAG_MISSING, item["key"], [{"normalized_value": reason}]),
                    }
                )
            continue
        if all(required in present for required in item["requires"]):
            continue
        absent = [name for name in item["requires"] if name not in present]
        reason = (
            f"checklist gap: {item['label']} is not in the registered documents "
            f"(absent: {', '.join(absent)}). No value was invented."
        )
        drafts.append(
            {
                "flag_type": FLAG_MISSING,
                "fact_type": item["key"],
                "checklist_key": item["key"],
                "blocks_ready": 0,
                "gap_reason": reason,
                "display_fact_id": None,
                "document_id": None,
                "members": [],
                "signature": _signature(FLAG_MISSING, item["key"], [{"normalized_value": item["key"]}]),
            }
        )

    for problem in problems:
        reason = problem.get("reason") or "Text could not be read. No facts were invented."
        members = []
        drafts.append(
            {
                "flag_type": FLAG_INCOMPLETE,
                "fact_type": "extraction",
                "checklist_key": "extraction",
                "blocks_ready": 1,
                "gap_reason": reason,
                "display_fact_id": None,
                "document_id": problem.get("document_id"),
                "members": members,
                "signature": _signature(
                    FLAG_INCOMPLETE,
                    str(problem.get("document_id")),
                    [{"normalized_value": reason, "document_id": problem.get("document_id")}],
                ),
            }
        )
    return drafts
