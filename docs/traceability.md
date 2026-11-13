# Traceability

Requirement ids are the Assignment 2 set. "Code" is the module that enforces the rule. "Test" is the pytest name.

| ID | What it asks for | Code | Test |
| --- | --- | --- | --- |
| FR-01 | Register each file before extraction. Unsupported files yield no facts. | `IntakeService._store_file`, `extract` | `test_fr01_registration_record_before_facts_and_unsupported_yields_none` |
| FR-02 | One matter id. A mismatched file stays out. | `IntakeService._classify` | `test_fr02_matter_binding_holds_out_a_mismatched_file` |
| FR-03 | Keep bytes and prior versions. | `open_source`, `add_document` | `test_fr03_reupload_keeps_the_prior_version` |
| FR-04 | Candidates carry type, value, source, locator, excerpt, status. | `extractors.py`, fact insert | `test_fr04_fr05_fr20_candidates_are_sourced_and_labeled` |
| FR-05 | Supplied vs Generated-Candidate. No Verified Evidence or Attorney Conclusion label. | `constants.py` labels | `test_fr04_fr05_fr20_candidates_are_sourced_and_labeled` |
| FR-06 | Flag missing checklist items. Do not invent values. | `flags.py` | `test_fr06_missing_items_are_flagged_and_not_invented` |
| FR-07 | Equivalent values are a duplicate. Both sources stay. | `flags.py`, `normalize.py` | `test_fr07_same_date_two_formats_is_a_duplicate`, `test_dates_and_times_normalize_equivalent_forms` |
| FR-08 | Incompatible values are a conflict. No winner. | `flags.py`, flags table | `test_fr08_conflict_has_no_winner` |
| FR-09 | Open questions do not ask for a legal conclusion. | `IntakeService._open_questions` | `test_fr09_fr17_open_questions_are_internal` |
| FR-10 | Accept, edit, reject, split, with a trail. | review methods in `service.py` | `test_fr10_edit_reject_and_split_leave_a_trail` |
| FR-11 | Flag dispositions. An open conflict blocks Ready. | `dispose_flag`, `gate.py` | `test_fr11_fr15_ready_is_blocked_until_staff_and_the_gate` |
| FR-12 | Seven sections, no legal-advice section. | `report.py` | `test_fr12_fr13_fr14_report_uses_approved_facts_and_sources` |
| FR-13 | Approved section is approved facts only. | `report.compose` drops candidates for the attorney view | `test_fr12_fr13_fr14_report_uses_approved_facts_and_sources` |
| FR-14 | Document id, type, locator, and excerpt for approved facts and conflict values. | `report.py` | `test_fr12_fr13_fr14_report_uses_approved_facts_and_sources` |
| FR-15 | Status lifecycle. Unattended extraction stops at Candidates extracted. | `TRANSITIONS`, `mark_ready` | `test_fr11_fr15_ready_is_blocked_until_staff_and_the_gate` |
| FR-16 | Do not adopt fault language as a finding. | quotations in `extractors.py`, `prohibited.py` | `test_fr16_fault_language_stays_a_quotation`, `test_prohibited_edit_is_not_approved` |
| FR-17 | No outbound messages. | `outbound_message_count` | `test_fr09_fr17_open_questions_are_internal` |
| FR-18 | Unreadable file: register it, invent nothing, block. | `looks_unreadable`, incomplete flag | `test_fr18_unreadable_file_invents_nothing`, `test_remote_payload_drops_unsourced_and_approved_flags` |
| FR-19 | A later conflicting document keeps history and returns the matter. | `_return_if_blocking` | `test_fr19_new_document_keeps_history_and_returns_the_matter` |
| FR-20 | Generated items stay candidates until a person accepts them. | fact status and review decisions | `test_fr04_fr05_fr20_candidates_are_sourced_and_labeled` |
| FR-21 | User, role, and matter on every call. Cross-matter reads return nothing. | `_can_read`, `_guard` | `test_fr21_cross_matter_access_returns_no_records` |
| NFR-01 | Registration through draft within 120 seconds, with a stage. | `stage_events` | `test_nfr01_nfr02_bundle_and_staff_action_are_within_limits` |
| NFR-02 | A staff action persists within 3 seconds. | review methods | `test_nfr01_nfr02_bundle_and_staff_action_are_within_limits` |
| NFR-03 | Sources, or an explicit checklist-gap reason. | fact columns, `gap_reason` | `test_fr04_fr05_fr20_candidates_are_sourced_and_labeled`, `test_fr06_missing_items_are_flagged_and_not_invented` |
| NFR-04 | Regeneration does not rewrite excerpts. | report reads stored excerpt | `test_fr12_fr13_fr14_report_uses_approved_facts_and_sources` |
| NFR-05 | Synthetic data. No cross-matter records. | samples, access checks | `test_fr21_cross_matter_access_returns_no_records` |
| NFR-06 | Mask government ids and account numbers. Reveal needs a reason. | `masking.py`, `reveal` | `test_nfr06_default_report_masks_identifiers` |
| NFR-07 | Unauthenticated access is denied. Changes name a user. | web session, audit columns | `test_health_and_sign_in_wall`, `test_fr10_edit_reject_and_split_leave_a_trail` |
| NFR-08 | Matter text only to named tools. | `tools.invoke` | `test_nfr08_nfr12_unlisted_tool_is_refused` |
| NFR-09 | One current status and a review record. Ready stores staff identity. | `matters.status`, `review_decisions` | `test_fr11_fr15_ready_is_blocked_until_staff_and_the_gate` |
| NFR-10 | Seven sections in order, conflicts separated, short enough to scan. | `report.py` | `test_fr12_fr13_fr14_report_uses_approved_facts_and_sources`, `test_sample_hale_duplicate_does_not_block_ready` |
| NFR-11 | Allowed help vs prohibited legal action. | gate, `ask`, no mail tool | `test_fr16_fault_language_stays_a_quotation`, `test_nfr15_gate_fails_closed_without_the_notice` |
| NFR-12 | Named, versioned tools only. | `ALLOWED_TOOLS` | `test_nfr08_nfr12_unlisted_tool_is_refused` |
| NFR-13 | Required labels on inputs and outputs. | constants and fact insert | `test_fr04_fr05_fr20_candidates_are_sourced_and_labeled` |
| NFR-14 | People, not the extractor, complete the approval points. | no Ready path inside extract | `test_fr11_fr15_ready_is_blocked_until_staff_and_the_gate` |
| NFR-15 | Gate checks before Ready. | `gate.assess`, `mark_ready` | `test_nfr15_gate_fails_closed_without_the_notice`, `test_fr11_fr15_ready_is_blocked_until_staff_and_the_gate` |
| NFR-16 | Version pin on the matter. | `checklist.py` | `test_nfr16_checklist_version_stays_on_the_matter` |
| NFR-17 | Attorney reviewed means the attorney has seen the report. | `acknowledge` | `test_attorney_acknowledgment_does_not_change_facts` |
| NFR-18 | Append-only audit. A fact change can be rebuilt from the log. | SQL triggers, edit audit detail | `test_nfr18_audit_log_is_append_only` |
| NFR-19 | One evidence pack for the validation questions. | this table plus `pytest` | `docs/validation.md` |
| NFR-20 | Locked notice on every report. | `BOUNDARY_NOTICE` | `test_nfr15_gate_fails_closed_without_the_notice`, `test_sample_hale_duplicate_does_not_block_ready` |

The web and command-line checks are `test_staff_sees_a_blocked_conflict_and_attorney_sees_no_candidates` and `test_cli_loads_samples_and_prints_a_report`.
