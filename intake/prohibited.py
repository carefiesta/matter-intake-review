"""Spot legal-conclusion language outside attributed quotations."""

from __future__ import annotations

import re

from intake.constants import PROHIBITED_PATTERNS


def find_prohibited(text: str) -> list[str]:
    hits: list[str] = []
    for pattern, label in PROHIBITED_PATTERNS:
        if re.search(pattern, text or "", flags=re.IGNORECASE):
            if label not in hits:
                hits.append(label)
    return hits
