"""Mask government IDs and account numbers on the default report. NFR-06."""

from __future__ import annotations

import re

SSN_RE = re.compile(r"\b(\d{3})-(\d{2})-(\d{4})\b")
LONG_DIGITS = re.compile(r"\d{8,17}")


def mask_ssn_text(text: str) -> str:
    return SSN_RE.sub(lambda m: f"***-**-{m.group(3)}", text)


def mask_fact_value(value: str, fact_type: str | None) -> str:
    if fact_type == "government_id" or SSN_RE.search(value):
        return mask_ssn_text(value)
    if fact_type == "account_number":
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 8:
            return ("*" * (len(digits) - 4)) + digits[-4:]
        return "****"
    return value


def mask_report_text(text: str) -> str:
    """Mask identifiers in the attorney-facing text, including excerpts."""
    masked = mask_ssn_text(text)

    def hide_account(match: re.Match[str]) -> str:
        digits = match.group(0)
        # Leave short line numbers alone. Eight or more digits are treated
        # as an account-style value on the report surface.
        return ("*" * (len(digits) - 4)) + digits[-4:]

    return LONG_DIGITS.sub(hide_account, masked)
