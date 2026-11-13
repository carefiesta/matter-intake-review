# Architecture

The prototype is the Document Intelligence pipeline from Assignment 2, with the quality gate left as its own step. A single chat summary was rejected in that design because it cannot carry status, source, or the Ready check. Case, knowledge, and decision architectures were rejected because they pull legal judgment into the system.

## Components

| Component | Responsibility | May not |
| --- | --- | --- |
| Access guard | Check user, role, and matter on every call | Be skipped |
| Ingestion | Register the bundle, bind the matter, return ids and rejection reasons | Extract facts |
| Document store | Keep the original bytes, the version, and a locator. Label Supplied | Overwrite a version |
| Extraction service | Propose source-linked candidates and the first flags. Status Candidate | Set Approved or Ready |
| Fact store | Keep candidates apart from approved facts. Keep flags | Choose a conflict winner |
| Review workbench | Accept, edit, reject, split, dispose | Release the report by itself |
| Quality gate | Pass or fail with reasons | Be skipped by the report |
| Report service | Seven sections from approved information, plus the locked notice | Invent a fact, or copy a candidate into the attorney body |
| Attorney reviewer | Outside. Read, acknowledge, or return | Write a fact |

Code map:

| Piece | Module |
| --- | --- |
| Access context | `intake/access.py`, checks in `intake/service.py` |
| Document store and registration | `intake/service.py`, schema in `intake/db.py` |
| Extractor interface and local rules | `intake/extractors.py` |
| Optional remote model | `RemoteExtractor` in `intake/extractors.py` |
| Tool allow list | `intake/tools.py`, `ALLOWED_TOOLS` in `intake/constants.py` |
| Flag rules | `intake/flags.py` |
| Quality gate | `intake/gate.py` |
| Report | `intake/report.py` |
| Masking | `intake/masking.py` |
| Web workbench | `intake/web.py`, `intake/templates/` |
| Command line | `intake/cli.py` |

## How a matter moves

Allowed statuses, in the FR-15 set:

1. Documents registered
2. Candidates extracted
3. Staff review in progress
4. Corrections applied
5. Ready for attorney
6. Returned for revision
7. Attorney reviewed

Extraction stops at Candidates extracted. Only intake staff can set Ready for attorney, and only when `gate.assess` returns no reasons. The attorney sets Attorney reviewed or Returned for revision. That write does not update a fact.

A new blocking flag after release sets Returned for revision and leaves the old review rows in place.

## Information

A document locator is `document id / version`. A fact locator adds the page and line, for example `D-0004/v1/p1/L6`. The excerpt is the line that was stored at extraction time. Regenerating the report reads that stored excerpt. It does not ask the extractor to rewrite it.

Sensitive values (a government id, a long account number) are masked on the report. Reveal writes an audit row and still does not unmask the default report.

## Rules that are easy to get wrong

- Equivalent dates are a duplicate. Incompatible dates are a conflict. The conflict record has no winning-value column.
- A duplicate keeps every source and names one display instance. It does not block Ready.
- An open conflict blocks Ready. So does an unreadable file. A missing checklist item becomes an open question and does not, by itself, block Ready.
- Fault language in a source is stored as a supplied quotation. It is not a candidate fact. The prohibition scan ignores the source-reference section and fails the gate if the same wording shows up in the rest of the report or in an approved value.
- There is no outbound count other than zero. Missing contact details do not create an email.

## What is checked before Ready

From NFR-15, as implemented in `intake/gate.py`:

- The caller is intake staff.
- Every current document is extracted, unprocessed, held out, or extraction-incomplete.
- Every approved fact has a document, a locator, and an excerpt that is still in that document.
- No approved fact uses the labels Verified Evidence or Attorney Conclusion.
- Every open conflict and every open extraction-incomplete flag has a disposition.
- Every duplicate has a display instance and at least two sources.
- The prohibition scan is clean outside quotations and source excerpts.
- The locked notice is in the report text.

Failed checks return reasons. They do not change the status. `mark_ready` is the only path that sets Ready, and it refuses to do so when the list of reasons is not empty.
