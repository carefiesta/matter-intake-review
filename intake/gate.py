"""Quality gate. The report composer cannot skip this or set Ready."""

from __future__ import annotations

from intake.constants import (
    BOUNDARY_NOTICE,
    FACT_APPROVED,
    FLAG_CONFLICT,
    FLAG_DUPLICATE,
    FLAG_INCOMPLETE,
    FORBIDDEN_LABELS,
    LABEL_APPROVED,
    STAFF,
)
from intake.prohibited import find_prohibited


def assess(snapshot: dict) -> list[str]:
    """Return human-readable reasons. An empty list means the gate passed.

    `snapshot` is plain data so tests can show a failing check on purpose.
    """
    reasons: list[str] = []
    if snapshot.get("role") != STAFF or not snapshot.get("user_id"):
        reasons.append("Ready requires an intake staff identity")

    for document in snapshot.get("documents") or []:
        if document.get("is_current") and document.get("status") not in (
            "extracted",
            "unprocessed",
            "held_out",
            "extraction_incomplete",
        ):
            reasons.append(
                f"Document {document.get('filename')} is not registered or excepted"
            )

    for fact in snapshot.get("approved_facts") or []:
        if fact.get("status") != FACT_APPROVED:
            reasons.append(f"Approved section contains non-approved fact {fact.get('fact_id')}")
        if not fact.get("document_id") or not fact.get("locator") or not fact.get("excerpt"):
            reasons.append(f"Approved fact {fact.get('fact_id')} is missing a source")
        elif fact.get("excerpt_in_source") is False:
            reasons.append(f"Approved fact {fact.get('fact_id')} excerpt is not in the source")
        if fact.get("label") in FORBIDDEN_LABELS:
            reasons.append(f"Fact {fact.get('fact_id')} uses a forbidden label")
        if fact.get("label") not in (LABEL_APPROVED, "Generated-Candidate"):
            reasons.append(f"Fact {fact.get('fact_id')} has an unexpected label")

    for flag in snapshot.get("flags") or []:
        if not flag.get("active"):
            continue
        if flag.get("disposition"):
            continue
        if flag.get("flag_type") == FLAG_CONFLICT and flag.get("blocks_ready"):
            reasons.append(
                f"Open conflict on {flag.get('fact_type')} has no disposition and no winning value"
            )
        if flag.get("flag_type") == FLAG_INCOMPLETE and flag.get("blocks_ready"):
            reasons.append("Open extraction-incomplete flag blocks Ready")
        if flag.get("flag_type") == FLAG_DUPLICATE:
            if not flag.get("display_fact_id"):
                reasons.append(f"Duplicate {flag.get('fact_type')} has no display instance")
            members = flag.get("members") or []
            if len(members) < 2:
                reasons.append(f"Duplicate {flag.get('fact_type')} does not keep both sources")

    hits = find_prohibited(snapshot.get("scan_text") or "")
    if hits:
        reasons.append("Prohibition scan failed: " + ", ".join(hits))

    notice = snapshot.get("notice_text") or ""
    if BOUNDARY_NOTICE not in notice:
        reasons.append("Boundary notice is missing")

    # Preserve order but drop repeats.
    unique: list[str] = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    return unique
