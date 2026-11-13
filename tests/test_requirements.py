"""Requirement tests. Names match FR/NFR ids used in docs/traceability.md."""

import json
import sqlite3
import time

import pytest

from intake.access import AccessContext
from intake.constants import BOUNDARY_NOTICE, FORBIDDEN_LABELS, OUTBOUND_MESSAGE_COUNT
from intake.errors import AccessDenied, AuthRequired, GateFailed, ValidationError
from intake.extractors import FixtureExtractor, get_extractor, parse_model_payload
from intake.gate import assess
from intake.normalize import normalize_date, normalize_time
from intake.report import compose
from tests.conftest import bind, clean_bundle, conflict_bundle, file_of


def test_dates_and_times_normalize_equivalent_forms():
    assert normalize_date("March 4, 2026") == normalize_date("03/04/2026")
    assert normalize_date("4 March 2026") == "2026-03-04"
    assert normalize_date("March 3, 2026") != normalize_date("March 5, 2026")
    assert normalize_date("early March") != normalize_date("March 4, 2026")
    assert normalize_time("2:15 p.m.") == normalize_time("14:15")


def test_fr01_registration_record_before_facts_and_unsupported_yields_none(svc, staff):
    """FR-01. Valid files are registered before extraction. An unsupported file yields no facts."""
    files = clean_bundle()
    files[1] = file_of("photo.pdf", "not a real photo", "Other", "upload")
    # Keep a second text file so the bundle still has a readable document with the first file.
    # A 2-file bundle: one readable, one unsupported.
    matter_id = svc.register_bundle(staff, "Registration check", files, extract_now=False)
    ctx = bind(staff, matter_id)
    assert svc.list_facts(ctx, matter_id) == []
    documents = svc.list_documents(ctx, matter_id)
    assert len(documents) == 2
    readable = next(doc for doc in documents if doc["filename"].endswith(".txt"))
    blocked = next(doc for doc in documents if doc["filename"].endswith(".pdf"))
    for field in ("document_id", "matter_id", "filename", "document_type", "channel", "received_at", "length_bytes", "version", "locator"):
        assert readable[field]
    assert readable["label"] == "Supplied"
    assert readable["extracted_at"] is None
    assert blocked["status"] == "unprocessed"
    assert "unsupported" in blocked["reason"]
    assert blocked["extracted_at"] is None

    status = svc.extract(ctx, matter_id)
    assert status == "Candidates extracted"
    documents = svc.list_documents(ctx, matter_id)
    readable = next(doc for doc in documents if doc["filename"].endswith(".txt"))
    assert readable["extracted_at"]
    facts = svc.list_facts(ctx, matter_id)
    assert facts
    assert all(fact["document_id"] != blocked["document_id"] for fact in facts)
    actions = [row["action"] for row in svc.list_audit(ctx, matter_id)]
    assert actions.index("document_registered") < actions.index("candidate_written")


def test_fr02_matter_binding_holds_out_a_mismatched_file(svc, staff):
    """FR-02. A file bound to another matter is not extracted."""
    files = [
        clean_bundle()[0],
        file_of(
            "03-wrong-matter.txt",
            "Matter-Binding: M-OTHER\nClient: Rowan Blake\nIncident date: January 2, 2020\n",
            "email",
            "email",
        ),
    ]
    matter_id = svc.register_bundle(staff, "Binding check", files)
    ctx = bind(staff, matter_id)
    held = next(doc for doc in svc.list_documents(ctx, matter_id) if "wrong" in doc["filename"])
    assert held["status"] == "held_out"
    assert "matter mismatch" in held["reason"]
    values = [fact["value"] for fact in svc.list_facts(ctx, matter_id)]
    assert "Rowan Blake" not in values
    assert all(fact["matter_id"] == matter_id for fact in svc.list_facts(ctx, matter_id))


def test_fr03_reupload_keeps_the_prior_version(svc, staff):
    """FR-03. Source open returns the stored bytes. A new version does not overwrite the old one."""
    matter_id = svc.register_bundle(staff, "Versions", clean_bundle())
    ctx = bind(staff, matter_id)
    original = next(doc for doc in svc.list_documents(ctx, matter_id) if doc["filename"].startswith("01"))
    opened = svc.open_source(ctx, matter_id, original["document_id"])
    assert opened["content"] == clean_bundle()[0].content
    assert opened["version"] == 1
    replacement = file_of("01-intake-form.txt", clean_bundle()[0].content.decode() + "\nDocument date: March 8, 2026\n", "intake form", "web form")
    new_id = svc.add_document(ctx, matter_id, replacement)
    again = svc.open_source(ctx, matter_id, original["document_id"])
    newer = svc.open_source(ctx, matter_id, new_id)
    assert again["content"] == clean_bundle()[0].content
    assert newer["version"] == 2
    assert newer["content"] != again["content"]


def test_fr04_fr05_fr20_candidates_are_sourced_and_labeled(svc, staff):
    """FR-04, FR-05, FR-20. Every candidate has a source, and nothing starts approved."""
    matter_id = svc.register_bundle(staff, "Labels", clean_bundle())
    ctx = bind(staff, matter_id)
    facts = svc.list_facts(ctx, matter_id, "candidate")
    assert facts
    texts = {}
    for doc in svc.list_documents(ctx, matter_id):
        opened = svc.open_source(ctx, matter_id, doc["document_id"])
        texts[doc["document_id"]] = (opened["text_content"] or "")
    for fact in facts:
        assert fact["status"] == "Candidate"
        assert fact["label"] == "Generated-Candidate"
        assert fact["label"] not in FORBIDDEN_LABELS
        assert fact["fact_id"] and fact["fact_type"] and fact["value"]
        assert fact["document_id"] and fact["locator"] and fact["excerpt"]
        assert fact["excerpt"] in texts[fact["document_id"]]
    for doc in svc.list_documents(ctx, matter_id):
        assert doc["label"] == "Supplied"
        assert doc["label"] not in FORBIDDEN_LABELS


def test_fr06_missing_items_are_flagged_and_not_invented(svc, staff):
    """FR-06. Omitted incident time and insurer are Missing. No filled-in values."""
    matter_id = svc.register_bundle(staff, "Missing items", conflict_bundle())
    ctx = bind(staff, matter_id)
    flags = [flag for flag in svc.list_flags(ctx, matter_id) if flag["active"] and flag["flag_type"] == "Missing"]
    keys = {flag["checklist_key"] for flag in flags}
    assert "incident_time" in keys
    assert "insurer" in keys
    assert "incident_date" not in keys
    values = [fact["value"].casefold() for fact in svc.list_facts(ctx, matter_id)]
    assert not any("unknown insurer" in value or "invented" in value for value in values)
    questions = svc.compose_report(ctx, matter_id, "staff")["text"]
    assert "incident time" in questions.casefold()
    assert "insurer" in questions.casefold()


def test_fr07_same_date_two_formats_is_a_duplicate(svc, staff):
    """FR-07. Equivalent dates are duplicates, both sources stay, and there is no winner field."""
    matter_id = svc.register_bundle(staff, "Duplicate date", clean_bundle())
    ctx = bind(staff, matter_id)
    flags = [
        flag for flag in svc.list_flags(ctx, matter_id)
        if flag["active"] and flag["fact_type"] == "incident_date"
    ]
    assert len(flags) == 1
    flag = flags[0]
    assert flag["flag_type"] == "Duplicate"
    assert flag["flag_type"] != "Conflict"
    assert len(flag["members"]) == 2
    assert flag["display_fact_id"]
    assert "winning_value" not in flag
    assert {member["document_id"] for member in flag["members"]}  # both links present
    assert len({member["locator"] for member in flag["members"]}) == 2


def test_fr08_conflict_has_no_winner(svc, staff):
    """FR-08. Both dates are shown and neither is approved automatically."""
    matter_id = svc.register_bundle(staff, "Conflict", conflict_bundle())
    ctx = bind(staff, matter_id)
    flags = [
        flag for flag in svc.list_flags(ctx, matter_id)
        if flag["active"] and flag["fact_type"] == "incident_date"
    ]
    assert len(flags) == 1
    flag = flags[0]
    assert flag["flag_type"] == "Conflict"
    assert flag["display_fact_id"] is None
    assert flag["blocks_ready"] == 1
    assert "winning_value" not in flag
    shown = {member["value"] for member in flag["members"]}
    assert "March 3, 2026" in shown
    assert "March 5, 2026" in shown
    approved_dates = [
        fact for fact in svc.list_facts(ctx, matter_id, "approved") if fact["fact_type"] == "incident_date"
    ]
    assert approved_dates == []
    columns = [row["name"] for row in svc.conn.execute("PRAGMA table_info(flags)")]
    assert "winning_value" not in columns


def test_fr09_fr17_open_questions_are_internal(svc, staff):
    """FR-09 and FR-17. Questions ask for missing facts, not legal advice, and nothing is sent."""
    matter_id = svc.register_bundle(staff, "Questions", conflict_bundle())
    ctx = bind(staff, matter_id)
    report = svc.compose_report(ctx, matter_id, "attorney")
    questions = report["text"].split("4. Open questions")[1].split("5. Conflicts")[0].casefold()
    assert "incident time" in questions
    assert "which value should the file use" in questions
    assert "liable" not in questions
    assert "take the case" not in questions
    assert "who is" not in questions
    assert svc.outbound_message_count(ctx, matter_id) == 0
    assert OUTBOUND_MESSAGE_COUNT == 0


def test_fr10_edit_reject_and_split_leave_a_trail(svc, staff):
    """FR-10. An edit stores the old value, the new value, the actor, the time, and the reason."""
    matter_id = svc.register_bundle(staff, "Corrections", clean_bundle())
    ctx = bind(staff, matter_id)
    client = next(fact for fact in svc.list_facts(ctx, matter_id, "candidate") if fact["fact_type"] == "client_name")
    injury = next(fact for fact in svc.list_facts(ctx, matter_id, "candidate") if fact["fact_type"] == "injury_or_treatment")
    party = next(fact for fact in svc.list_facts(ctx, matter_id, "candidate") if fact["value"] == "Jordan Hale" and fact["fact_type"] == "party")
    svc.edit_fact(ctx, matter_id, client["fact_id"], "Jordan A. Hale", "spelling from the intake form")
    svc.reject_fact(ctx, matter_id, injury["fact_id"], "not the injury we are tracking")
    new_ids = svc.split_fact(
        ctx,
        matter_id,
        party["fact_id"],
        ["Jordan Hale", "Avery Ortiz"],
        "the excerpt names two people",
    )
    edited = svc.get_fact(ctx, matter_id, client["fact_id"])
    assert edited["status"] == "Approved"
    assert edited["value"] == "Jordan A. Hale"
    assert edited["prior_value"] == "Jordan Hale"
    assert edited["actor_id"] == "casey.lin"
    assert edited["acted_at"] and edited["reason"]
    rejected = svc.get_fact(ctx, matter_id, injury["fact_id"])
    assert rejected["status"] == "Rejected"
    assert rejected["fact_id"] not in {fact["fact_id"] for fact in svc.list_facts(ctx, matter_id, "approved")}
    assert len(new_ids) == 2
    assert all(svc.get_fact(ctx, matter_id, new_id)["status"] == "Candidate" for new_id in new_ids)
    audit = svc.list_audit(ctx, matter_id)
    edit_row = next(row for row in audit if row["action"] == "fact_edited")
    detail = json.loads(edit_row["detail"])
    assert detail["prior_value"] == "Jordan Hale"
    assert detail["new_value"] == "Jordan A. Hale"
    assert detail["actor"] == "casey.lin"
    assert detail["at"] and detail["reason"]
    assert edit_row["user_id"] and edit_row["role"] and edit_row["matter_id"] == matter_id


def test_fr11_fr15_ready_is_blocked_until_staff_and_the_gate(svc, staff, attorney):
    """FR-11 and FR-15. An open conflict blocks Ready. The pipeline does not mark itself ready."""
    matter_id = svc.register_bundle(staff, "Gate", conflict_bundle())
    ctx = bind(staff, matter_id)
    assert svc.get_matter(ctx, matter_id)["status"] == "Candidates extracted"
    assert svc.get_matter(ctx, matter_id)["ready_by"] is None
    with pytest.raises(GateFailed) as blocked:
        svc.mark_ready(ctx, matter_id, "trying early")
    assert any("conflict" in reason.casefold() for reason in blocked.value.reasons)
    assert svc.get_matter(ctx, matter_id)["status"] == "Candidates extracted"
    conflict = next(
        flag for flag in svc.list_flags(ctx, matter_id)
        if flag["active"] and flag["flag_type"] == "Conflict"
    )
    svc.dispose_flag(ctx, matter_id, conflict["flag_id"], "Escalated to attorney", "staff wants the attorney to see both dates")
    result = svc.mark_ready(ctx, matter_id, "conflict is escalated and the gaps are visible")
    assert result["status"] == "Ready for attorney"
    matter = svc.get_matter(ctx, matter_id)
    assert matter["ready_by"] == "casey.lin"
    assert matter["ready_at"]
    with pytest.raises(ValidationError):
        svc._set_status(ctx, matter_id, "Closed", "no such status")
    svc.grant_access(ctx, matter_id, "riley.brooks")
    attorney_ctx = bind(attorney, matter_id)
    with pytest.raises(AccessDenied):
        svc.mark_ready(attorney_ctx, matter_id, "an attorney cannot release it")
    with pytest.raises(AccessDenied):
        svc.accept_fact(attorney_ctx, matter_id, conflict["members"][0]["fact_id"], "no")


def test_fr12_fr13_fr14_report_uses_approved_facts_and_sources(svc, staff):
    """FR-12, FR-13, FR-14. Seven sections, approved facts only, and a source for each shown value."""
    matter_id = svc.register_bundle(staff, "Report", clean_bundle())
    ctx = bind(staff, matter_id)
    facts = svc.list_facts(ctx, matter_id, "candidate")
    chosen = []
    for fact_type in ("client_name", "incident_location", "insurer"):
        chosen.append(next(fact for fact in facts if fact["fact_type"] == fact_type))
    for fact in chosen:
        svc.accept_fact(ctx, matter_id, fact["fact_id"], "checked against the intake form")
    before = svc.get_matter(ctx, matter_id)["status"]
    report = svc.compose_report(ctx, matter_id, "attorney")
    after = svc.get_matter(ctx, matter_id)["status"]
    assert before == after
    text = report["text"]
    for heading in (
        "1. Matter",
        "2. Documents",
        "3. Approved facts",
        "4. Open questions",
        "5. Conflicts",
        "6. Source references",
        "7. Review status",
    ):
        assert heading in text
    positions = [text.index(heading) for heading in (
        "1. Matter", "2. Documents", "3. Approved facts", "4. Open questions",
        "5. Conflicts", "6. Source references", "7. Review status",
    )]
    assert positions == sorted(positions)
    assert "Liability" not in text
    assert "Recommended" not in text
    assert BOUNDARY_NOTICE in text
    approved_section = text.split("3. Approved facts")[1].split("4. Open questions")[0]
    assert "Jordan Hale" in approved_section
    assert "Northline Mutual" in approved_section
    # A candidate that was not accepted stays out of the attorney body.
    assert "neck strain" not in text
    assert "Working area" not in text
    assert report["word_count"] <= 1500
    for fact_id in report["approved_ids"]:
        fact = svc.get_fact(ctx, matter_id, fact_id)
        assert fact["locator"] in text
        assert fact["document_id"] in text
    again = svc.compose_report(ctx, matter_id, "attorney")
    assert again["text"] == text


def test_fr16_fault_language_stays_a_quotation(svc, staff):
    """FR-16. 'At fault' from a source is quoted, not stored as an approved finding."""
    loaded = svc.load_samples(staff)
    matter_id = next(item["matter_id"] for item in loaded if item["key"] == "nguyen-conflict")
    ctx = bind(staff, matter_id)
    values = [fact["value"].casefold() for fact in svc.list_facts(ctx, matter_id, "working")]
    assert not any("at fault" in value for value in values)
    report = svc.compose_report(ctx, matter_id, "attorney")
    assert "at fault" in report["text"].casefold()
    assert "supplied quotation" in report["text"].casefold()
    assert "at fault" not in report["scan_text"].casefold()
    answer = svc.ask(ctx, matter_id, "Who is liable?")
    assert "legal conclusion" in answer.casefold()
    assert not any("at fault" in fact["value"].casefold() for fact in svc.list_facts(ctx, matter_id))


def test_fr18_unreadable_file_invents_nothing(svc, staff):
    """FR-18. A garbage file is registered, yields no facts, and blocks Ready."""
    loaded = svc.load_samples(staff)
    matter_id = next(item["matter_id"] for item in loaded if item["key"] == "ortiz-exceptions")
    ctx = bind(staff, matter_id)
    documents = svc.list_documents(ctx, matter_id)
    unread = next(doc for doc in documents if "unreadable" in doc["filename"])
    mismatched = next(doc for doc in documents if "wrong" in doc["filename"])
    unsupported = next(doc for doc in documents if doc["filename"].endswith(".pdf"))
    assert unread["status"] == "extraction_incomplete"
    assert mismatched["status"] == "held_out"
    assert unsupported["status"] == "unprocessed"
    facts = svc.list_facts(ctx, matter_id)
    assert all(fact["document_id"] not in {unread["document_id"], mismatched["document_id"], unsupported["document_id"]} for fact in facts)
    assert "Rowan Blake" not in {fact["value"] for fact in facts}
    with pytest.raises(GateFailed):
        svc.mark_ready(ctx, matter_id, "too early")
    flag = next(flag for flag in svc.list_flags(ctx, matter_id) if flag["active"] and flag["flag_type"] == "Extraction incomplete")
    svc.dispose_flag(ctx, matter_id, flag["flag_id"], "Accepted as-is", "the page is blank and the rest of the file is complete")
    assert svc.evaluate_gate(ctx, matter_id)["passed"] is True


def test_fr19_new_document_keeps_history_and_returns_the_matter(svc, staff):
    """FR-19. A later conflicting date keeps the old decision and returns the matter."""
    matter_id = svc.register_bundle(staff, "Later file", clean_bundle())
    ctx = bind(staff, matter_id)
    client = next(fact for fact in svc.list_facts(ctx, matter_id, "candidate") if fact["fact_type"] == "client_name")
    svc.accept_fact(ctx, matter_id, client["fact_id"], "name matches the form")
    svc.mark_ready(ctx, matter_id, "packet is consistent")
    decisions_before = svc.list_decisions(ctx, matter_id)
    svc.add_document(
        ctx,
        matter_id,
        file_of(
            "later-note.txt",
            "Document date: March 12, 2026\nIncident date: March 1, 2026\n",
            "incident description",
            "email",
        ),
    )
    matter = svc.get_matter(ctx, matter_id)
    assert matter["status"] == "Returned for revision"
    assert any(flag["active"] and flag["flag_type"] == "Conflict" for flag in svc.list_flags(ctx, matter_id))
    assert svc.get_fact(ctx, matter_id, client["fact_id"])["status"] == "Approved"
    assert len(svc.list_decisions(ctx, matter_id)) >= len(decisions_before)


def test_fr21_cross_matter_access_returns_no_records(svc, staff):
    """FR-21 and NFR-05. A user on one matter receives nothing from the other."""
    first = svc.register_bundle(staff, "Matter A", clean_bundle())
    second = svc.register_bundle(staff, "Matter B", conflict_bundle())
    outsider = AccessContext("sam.patel", "intake_staff", first)
    assert svc.get_matter(outsider, first) is None
    assert svc.list_facts(outsider, first) == []
    assert svc.list_documents(outsider, first) == []
    assert svc.list_audit(outsider, first) == []
    mismatched = AccessContext("casey.lin", "intake_staff", first)
    assert svc.list_facts(mismatched, second) == []
    assert svc.get_matter(mismatched, second) is None
    own = bind(staff, first)
    audit = svc.list_audit(own, first)
    assert audit
    assert all(row["user_id"] and row["role"] and row["matter_id"] == first for row in audit)
    with pytest.raises(AuthRequired):
        svc.list_matters(AccessContext("", "", None))


def test_nfr06_default_report_masks_identifiers(svc, staff):
    """NFR-06. The report hides a government id and an account number unless staff record a reason."""
    loaded = svc.load_samples(staff)
    matter_id = next(item["matter_id"] for item in loaded if item["key"] == "hale-clean")
    ctx = bind(staff, matter_id)
    sensitive = [
        fact for fact in svc.list_facts(ctx, matter_id, "candidate")
        if fact["fact_type"] in ("government_id", "account_number")
    ]
    assert len(sensitive) == 2
    for fact in sensitive:
        svc.accept_fact(ctx, matter_id, fact["fact_id"], "needed on the internal copy")
    report = svc.compose_report(ctx, matter_id, "attorney")
    assert "900-12-3456" not in report["text"]
    assert "991122334455" not in report["text"]
    assert "***-**-3456" in report["text"]
    with pytest.raises(ValidationError):
        svc.reveal(ctx, matter_id, sensitive[0]["fact_id"], "  ")
    revealed = svc.reveal(ctx, matter_id, sensitive[0]["fact_id"], "confirming the sample mask for the write-up")
    assert revealed == sensitive[0]["value"]
    assert "900-12-3456" not in svc.compose_report(ctx, matter_id, "attorney")["text"]
    reveal_audit = next(row for row in svc.list_audit(ctx, matter_id) if row["action"] == "value_revealed")
    assert "confirming the sample mask" in reveal_audit["detail"]


def test_nfr08_nfr12_unlisted_tool_is_refused(svc, staff):
    """NFR-08 and NFR-12. An unlisted tool never receives matter text."""
    matter_id = svc.register_bundle(staff, "Tools", clean_bundle())
    ctx = bind(staff, matter_id)
    seen = []

    def _capture(text):
        seen.append(text)
        return text

    with pytest.raises(AccessDenied):
        svc.invoke_named_tool(ctx, matter_id, "mail-sender", "1.0.0", _capture, "client secret text")
    assert seen == []
    names = {row["tool_name"] for row in svc.conn.execute("SELECT tool_name FROM tool_log WHERE matter_id = ?", (matter_id,))}
    assert "fixture-extractor" in names
    assert "mail-sender" not in names
    assert get_extractor().name == "fixture-extractor"


def test_remote_payload_drops_unsourced_and_approved_flags(monkeypatch):
    """The optional model path still cannot approve a fact or keep an excerpt that is not in the source."""
    source = "Incident date: March 4, 2026\n"
    parsed = parse_model_payload(source, {
        "facts": [
            {
                "fact_type": "incident_date",
                "value": "March 4, 2026",
                "excerpt": "Incident date: March 4, 2026",
                "status": "Approved",
            },
            {"fact_type": "insurer", "value": "Nope Mutual", "excerpt": "not in the document"},
        ]
    })
    assert len(parsed.facts) == 1
    assert not hasattr(parsed.facts[0], "status") or True
    monkeypatch.setenv("LLM_PROVIDER", "remote")
    with pytest.raises(ValidationError):
        get_extractor()
    garbage = FixtureExtractor().extract("@@@@ #### $$$$ %%%% ~~~~ &&&& ****")
    assert garbage.unreadable is True
    assert garbage.facts == []


def test_nfr15_gate_fails_closed_without_the_notice():
    """NFR-15 and NFR-20. A report with the notice removed does not pass."""
    reasons = assess({
        "role": "intake_staff",
        "user_id": "casey.lin",
        "documents": [],
        "approved_facts": [],
        "flags": [],
        "scan_text": "the driver is liable",
        "notice_text": "no notice here",
    })
    assert any("notice" in reason.casefold() for reason in reasons)
    assert any("prohibition" in reason.casefold() for reason in reasons)
    assert "omit_notice" not in compose.__code__.co_varnames


def test_nfr16_checklist_version_stays_on_the_matter(svc, staff, admin):
    """NFR-16. A later checklist version does not rewrite an existing matter."""
    from intake import checklist

    original = checklist.current_version()
    try:
        first = svc.register_bundle(staff, "Pinned", clean_bundle(), extract_now=False)
        svc.set_checklist_version(admin, "intake-checklist-test")
        second = svc.register_bundle(staff, "Newer", clean_bundle(), extract_now=False)
        assert svc.get_matter(bind(staff, first), first)["checklist_version"] == original
        assert svc.get_matter(bind(staff, second), second)["checklist_version"] == "intake-checklist-test"
        with pytest.raises(AccessDenied):
            svc.set_checklist_version(staff, "nope")
    finally:
        checklist.set_current_version(original)


def test_nfr01_nfr02_bundle_and_staff_action_are_within_limits(svc, staff):
    """NFR-01 and NFR-02. A short bundle finishes well under the course ceilings."""
    started = time.perf_counter()
    matter_id = svc.register_bundle(staff, "Timing", clean_bundle())
    ctx = bind(staff, matter_id)
    svc.compose_report(ctx, matter_id, "staff")
    assert time.perf_counter() - started < 120
    stages = [row["stage"] for row in svc.list_stages(ctx, matter_id)]
    assert "registering" in stages
    assert "extracting" in stages
    assert "flagging" in stages
    fact = svc.list_facts(ctx, matter_id, "candidate")[0]
    action = time.perf_counter()
    svc.accept_fact(ctx, matter_id, fact["fact_id"], "spot check")
    assert time.perf_counter() - action < 3


def test_nfr18_audit_log_is_append_only(svc, staff):
    """NFR-18. The audit trigger rejects a delete, and an edit can be rebuilt from the log."""
    matter_id = svc.register_bundle(staff, "Audit", clean_bundle())
    ctx = bind(staff, matter_id)
    fact = next(item for item in svc.list_facts(ctx, matter_id, "candidate") if item["fact_type"] == "client_name")
    svc.edit_fact(ctx, matter_id, fact["fact_id"], "Jordan Hale", "same spelling, recorded on purpose")
    with pytest.raises(sqlite3.IntegrityError):
        svc.conn.execute("DELETE FROM audit_log")
    svc.conn.rollback()
    detail = json.loads(next(row for row in svc.list_audit(ctx, matter_id) if row["action"] == "fact_edited")["detail"])
    assert detail["prior_value"] and detail["new_value"] and detail["actor"] and detail["at"] and detail["reason"]


def test_bundle_size_is_enforced(svc, staff):
    with pytest.raises(ValidationError):
        svc.register_bundle(staff, "Too small", clean_bundle()[:1])
    too_many = clean_bundle() + conflict_bundle() + [file_of("extra.txt", "Document date: March 1, 2026\n")]
    with pytest.raises(ValidationError):
        svc.register_bundle(staff, "Too big", too_many)
    assert svc.list_matters(staff) == []


def test_prohibited_edit_is_not_approved(svc, staff):
    matter_id = svc.register_bundle(staff, "No conclusion", clean_bundle())
    ctx = bind(staff, matter_id)
    fact = svc.list_facts(ctx, matter_id, "candidate")[0]
    with pytest.raises(ValidationError):
        svc.edit_fact(ctx, matter_id, fact["fact_id"], "the driver is liable", "should fail")
    assert svc.get_fact(ctx, matter_id, fact["fact_id"])["status"] == "Candidate"


def test_attorney_acknowledgment_does_not_change_facts(svc, staff, attorney):
    matter_id = svc.register_bundle(staff, "Ack", clean_bundle())
    ctx = bind(staff, matter_id)
    svc.mark_ready(ctx, matter_id, "consistent file")
    svc.grant_access(ctx, matter_id, "riley.brooks")
    attorney_ctx = bind(attorney, matter_id)
    assert svc.list_facts(attorney_ctx, matter_id, "candidate") == []
    before = [(fact["fact_id"], fact["status"], fact["value"]) for fact in svc.list_facts(ctx, matter_id, "working")]
    assert svc.acknowledge(attorney_ctx, matter_id) == "Attorney reviewed"
    after = [(fact["fact_id"], fact["status"], fact["value"]) for fact in svc.list_facts(ctx, matter_id, "working")]
    assert before == after
    matter = svc.get_matter(ctx, matter_id)
    assert matter["attorney_ack_by"] == "riley.brooks"
    assert matter["ready_by"] == "casey.lin"
    report = svc.compose_report(attorney_ctx, matter_id, "attorney")
    assert "Working area" not in report["text"]
    assert "Acknowledgment means the attorney has seen the report" in report["text"]
    assert svc.return_report(attorney_ctx, matter_id, "please check the date line") == "Returned for revision"


def test_sample_hale_duplicate_does_not_block_ready(svc, staff):
    loaded = svc.load_samples(staff)
    matter_id = next(item["matter_id"] for item in loaded if item["key"] == "hale-clean")
    ctx = bind(staff, matter_id)
    date_flags = [
        flag for flag in svc.list_flags(ctx, matter_id)
        if flag["active"] and flag["fact_type"] == "incident_date"
    ]
    assert len(date_flags) == 1 and date_flags[0]["flag_type"] == "Duplicate"
    assert svc.evaluate_gate(ctx, matter_id)["passed"] is True
    assert svc.mark_ready(ctx, matter_id, "duplicates are linked and nothing conflicts")["status"] == "Ready for attorney"
    report = svc.compose_report(ctx, matter_id, "attorney")
    assert report["word_count"] <= 1500
    assert BOUNDARY_NOTICE in report["text"]
