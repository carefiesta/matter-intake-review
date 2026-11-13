"""Compose the seven-section intake report from approved records only."""

from __future__ import annotations

from intake.constants import BOUNDARY_NOTICE, FACT_LABELS, FLAG_CONFLICT
from intake.masking import mask_fact_value, mask_report_text


def _label(fact_type: str) -> str:
    return FACT_LABELS.get(fact_type, fact_type.replace("_", " "))


def _word_count(text: str) -> int:
    return len([part for part in text.split() if part.strip()])


def compose(data: dict) -> dict:
    """Build the report.

    `audience` is "attorney" or "staff". Candidates are ignored for the
    attorney text even if the caller passes them. There is no argument
    that removes the boundary notice.
    """
    audience = data.get("audience") or "attorney"
    if audience not in ("attorney", "staff"):
        audience = "attorney"

    documents = data.get("documents") or []
    doc_by_id = {doc["document_id"]: doc for doc in documents}
    approved = list(data.get("approved_facts") or [])
    flags = [flag for flag in (data.get("flags") or []) if flag.get("active")]
    quotes = data.get("quotes") or []
    matter = data["matter"]

    approved_ids = {fact["fact_id"] for fact in approved}
    hidden: set[str] = set()
    duplicate_sources: dict[str, list[dict]] = {}
    for flag in flags:
        if flag.get("flag_type") != "Duplicate":
            continue
        members = flag.get("members") or []
        approved_members = [m for m in members if m.get("fact_id") in approved_ids]
        if not approved_members:
            # Still remember the sources in case the display fact is approved later.
            continue
        display = flag.get("display_fact_id")
        keep = display if display in approved_ids else approved_members[0].get("fact_id")
        duplicate_sources[keep] = members
        for member in approved_members:
            if member.get("fact_id") != keep:
                hidden.add(member.get("fact_id"))

    shown = [fact for fact in approved if fact["fact_id"] not in hidden]

    def source_bits(document_id: str | None, locator: str | None, excerpt: str | None) -> str:
        doc = doc_by_id.get(document_id or "")
        doc_type = doc["document_type"] if doc else "unknown"
        filename = doc["filename"] if doc else document_id
        shown_excerpt = mask_fact_value(excerpt or "", None)
        # Account-shaped excerpts are masked again on the full text.
        return (
            f"{document_id} | {doc_type} | {filename} | {locator} | {shown_excerpt}"
        )

    scan_parts: list[str] = []
    lines: list[str] = [BOUNDARY_NOTICE, ""]

    lines.append("1. Matter")
    lines.append(f"Matter ID: {matter['matter_id']}")
    lines.append(f"Title: {matter['title']}")
    scan_parts.extend(lines)

    lines.append("")
    lines.append("2. Documents")
    if not documents:
        lines.append("(no documents)")
    for doc in documents:
        if not doc.get("is_current"):
            continue
        reason = f" | note: {doc['reason']}" if doc.get("reason") else ""
        lines.append(
            f"- {doc['document_id']} | {doc['document_type']} | {doc['filename']} | "
            f"version {doc['version']} | {doc['channel']} | {doc['status']}{reason}"
        )

    lines.append("")
    lines.append("3. Approved facts")
    if not shown:
        lines.append("(none yet)")
    for fact in shown:
        display = mask_fact_value(fact["value"], fact["fact_type"])
        lines.append(f"- { _label(fact['fact_type']) }: {display}")
        extra = duplicate_sources.get(fact["fact_id"]) or []
        if len(extra) > 1:
            lines.append("  Stated in more than one source. Both sources are kept.")

    lines.append("")
    lines.append("4. Open questions")
    questions = list(data.get("open_questions") or [])
    if not questions:
        lines.append("(none)")
    for question in questions:
        lines.append(f"- {question}")

    lines.append("")
    lines.append("5. Conflicts")
    conflicts = [flag for flag in flags if flag.get("flag_type") == FLAG_CONFLICT]
    if not conflicts:
        lines.append("(none)")
    for flag in conflicts:
        lines.append(f"- {_label(flag['fact_type'])}")
        lines.append("  No winning value is selected.")
        disposition = flag.get("disposition") or "(no disposition yet)"
        lines.append(f"  Disposition: {disposition}")
        for member in flag.get("members") or []:
            lines.append(
                "  - "
                + mask_fact_value(member.get("value") or "", flag["fact_type"])
                + " | "
                + source_bits(member.get("document_id"), member.get("locator"), member.get("excerpt"))
            )

    # Sections 1-5 and 7 are scanned. Section 6 holds supplied excerpts and
    # may quote source language such as "at fault" without failing the gate.
    scan_body = "\n".join(lines)

    lines.append("")
    lines.append("6. Source references")
    if not shown and not conflicts and not quotes:
        lines.append("(none)")
    for fact in shown:
        lines.append(
            f"- {fact['fact_id']} | {_label(fact['fact_type'])} | "
            + mask_fact_value(fact["value"], fact["fact_type"])
            + " | "
            + source_bits(fact["document_id"], fact["locator"], fact["excerpt"])
        )
        for member in duplicate_sources.get(fact["fact_id"]) or []:
            if member.get("fact_id") == fact["fact_id"]:
                continue
            lines.append(
                "  also | "
                + source_bits(member.get("document_id"), member.get("locator"), member.get("excerpt"))
            )
    for flag in conflicts:
        for member in flag.get("members") or []:
            lines.append(
                f"- conflict {_label(flag['fact_type'])} | "
                + mask_fact_value(member.get("value") or "", flag["fact_type"])
                + " | "
                + source_bits(member.get("document_id"), member.get("locator"), member.get("excerpt"))
            )
    for quote in quotes:
        lines.append(
            f"- supplied quotation | {quote['label']} | "
            + source_bits(quote["document_id"], quote["locator"], quote["excerpt"])
        )

    lines.append("")
    lines.append("7. Review status")
    lines.append(f"Status: {matter['status']}")
    lines.append(f"Marked ready by: {matter.get('ready_by') or '(not recorded)'}")
    lines.append(f"Marked ready at: {matter.get('ready_at') or '(not recorded)'}")
    if matter.get("attorney_ack_by"):
        lines.append(
            f"Attorney acknowledgment: {matter['attorney_ack_by']} at {matter.get('attorney_ack_at')}"
        )
        lines.append("Acknowledgment means the attorney has seen the report. It is not a case decision.")
    if matter.get("return_reason"):
        lines.append(f"Return note: {matter['return_reason']}")
    lines.append(f"Checklist version: {matter['checklist_version']}")
    lines.append(f"Template version: {matter['template_version']}")

    lines.append("")
    lines.append(BOUNDARY_NOTICE)

    staff_note = ""
    if audience == "staff":
        candidates = data.get("candidates") or []
        extra_lines = [
            "",
            "Working area (not part of the attorney packet)",
            "These candidates are not approved facts.",
        ]
        if not candidates:
            extra_lines.append("(no candidates waiting)")
        for fact in candidates:
            extra_lines.append(
                f"- {fact['fact_id']} | {_label(fact['fact_type'])} | "
                + mask_fact_value(fact["value"], fact["fact_type"])
                + f" | {fact['status']} | {fact['label']}"
            )
        staff_note = "\n".join(extra_lines)

    full = mask_report_text("\n".join(lines) + staff_note)
    scan_text = mask_report_text(scan_body + "\n" + BOUNDARY_NOTICE)
    # Status lines are part of the scan so a legal conclusion cannot hide there.
    scan_text = scan_text + "\n" + "\n".join(lines[lines.index("7. Review status") :])

    return {
        "matter_id": matter["matter_id"],
        "title": matter["title"],
        "status": matter["status"],
        "template_version": matter["template_version"],
        "audience": audience,
        "notice": BOUNDARY_NOTICE,
        "text": full,
        "scan_text": scan_text,
        "word_count": _word_count(full),
        "approved_ids": [fact["fact_id"] for fact in shown],
    }
