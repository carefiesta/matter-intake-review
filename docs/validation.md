# Validation evidence

Run this from the repository root after setup:

```bash
pytest
```

That is the evidence pack for FR-01 through FR-21 and NFR-01 through NFR-20. The mapping from each id to a test is in [traceability.md](traceability.md). Nothing in that run depends on a hidden server or a key. `LLM_PROVIDER` is forced to `fixture` for the suite.

The three sample bundles are the same files the workbench loads.

| Situation from Assignment 1 | Where it shows up |
| --- | --- |
| S1 Consistent bundle, then Ready | Hale sample, `test_sample_hale_duplicate_does_not_block_ready` |
| S2 Missing incident time and insurer, nothing invented | Nguyen sample and `test_fr06_missing_items_are_flagged_and_not_invented` |
| S3 Same date, two formats | Hale sample and `test_fr07_same_date_two_formats_is_a_duplicate` |
| S4 Two different incident dates, no winner | Nguyen sample and `test_fr08_conflict_has_no_winner` |
| S5 A source says someone was at fault | Nguyen email, `test_fr16_fault_language_stays_a_quotation` |
| S6 Unreadable file | Ortiz sample, `test_fr18_unreadable_file_invents_nothing` |
| S7 A later document after approval | `test_fr19_new_document_keeps_history_and_returns_the_matter` and `samples/nguyen-conflict/later-insurer-note.txt` |
| S8 "Who is liable?" | `test_fr16_fault_language_stays_a_quotation` |

## What the suite shows

- A fresh database loads three matters and stops at Candidates extracted. Nobody has marked them ready.
- Hale's gate passes. The date pair is a duplicate.
- Nguyen's gate fails until the conflict is disposed. The failure text says there is no winning value.
- Ortiz does not extract the mismatched file, the `.pdf`, or the unreadable file.
- Cross-matter and signed-out requests do not return the other matter's facts.
- An attorney acknowledgment does not change fact rows.

## What is still a manual check

NFR-10 asks whether a person can find the matter id, approved facts, conflicts, open questions, sources, and status within 60 seconds. The report uses those seven headings in that order, and the word count for the sample bundles stays under 1,500. Timing a reader is a demo step, not something the automated suite can honestly claim.

NFR-01 asks for a progress state if processing takes longer than 10 seconds. The fixture extractor finishes faster than that on these files. The workbench still shows the latest stage (registering, extracting, flagging, composing) so a slower provider would have a place to show progress.

## Observed limits

The local rules miss unlabeled paragraphs. A remote model, if you enable one, still cannot approve a fact: excerpts that are not in the source are dropped, and status is applied only by a staff action. That parser is tested without calling a network.

I have not timed this on a real four-document office scan. The 120-second figure is the course ceiling, and the suite only shows that these synthetic files finish well under it.
