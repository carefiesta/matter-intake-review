# Contributing

This is a course prototype. Small, reviewable changes are welcome.

## Setup

Follow the setup steps in the README. Run `pytest` before you open a pull request. The GitHub workflow runs the same tests.

## What to keep intact

- Requirement ids FR-01 through FR-21 and NFR-01 through NFR-20 should stay traceable. If you change a behavior, update the test and `docs/traceability.md` in the same change.
- Candidates stay candidates until a person accepts, edits, or rejects them.
- A conflict has no winning value.
- The report's approved section is built from approved facts only.
- The boundary notice stays in the template. Do not add a switch that removes it.
- Matter text goes only to the tools named in `intake/constants.py`. Do not add a mail sender or a tool that sets Ready.
- Do not commit `.env`, API keys, or real client documents. Sample files stay fictional.

## Demo accounts

The seeded password is for local grading. Do not treat it as a production secret, and do not point this app at a real matter file.

## Commit style

Write a short sentence about the behavior you changed. Mention the requirement id when the change is about one requirement.
