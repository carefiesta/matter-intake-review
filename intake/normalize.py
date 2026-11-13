"""Normalize fact values so duplicates and conflicts are comparable.

Q2 in the design is still open. This prototype treats two dates as the
same only when they fall on the same calendar day. "Early March" is not
the same as a specific day, so it will conflict with one.
"""

from __future__ import annotations

import re

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _ymd(year: int, month: int, day: int) -> str | None:
    if not (1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100):
        return None
    # Reject impossible days such as 31 April without pulling in calendar edge cases.
    thirty = {4, 6, 9, 11}
    if month in thirty and day > 30:
        return None
    if month == 2 and day > 29:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalize_date(value: str) -> str:
    text = value.strip()
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        found = _ymd(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        if found:
            return found
    slash = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if slash:
        found = _ymd(int(slash.group(3)), int(slash.group(1)), int(slash.group(2)))
        if found:
            return found
    named = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if named and named.group(1).casefold() in MONTHS:
        found = _ymd(int(named.group(3)), MONTHS[named.group(1).casefold()], int(named.group(2)))
        if found:
            return found
    dmy = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if dmy and dmy.group(2).casefold() in MONTHS:
        found = _ymd(int(dmy.group(3)), MONTHS[dmy.group(2).casefold()], int(dmy.group(1)))
        if found:
            return found
    return "raw:" + re.sub(r"\s+", " ", text.casefold())


def normalize_time(value: str) -> str:
    text = value.strip()
    text = re.sub(r"[.]", "", text)
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)?", text, flags=re.IGNORECASE)
    if not match:
        return "raw:" + re.sub(r"\s+", " ", text.casefold())
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = (match.group(3) or "").casefold()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if not ampm and hour > 23:
        return "raw:" + text.casefold()
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return "raw:" + text.casefold()
    return f"{hour:02d}:{minute:02d}"


def normalize_name(value: str) -> str:
    text = value.casefold()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_digits(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 7:
        return digits
    return re.sub(r"\s+", " ", value.casefold()).strip()


def normalize_generic(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


NORMALIZERS = {
    "client_name": normalize_name,
    "party": normalize_name,
    "incident_date": normalize_date,
    "document_date": normalize_date,
    "incident_time": normalize_time,
    "client_contact": normalize_digits,
    "government_id": normalize_digits,
    "account_number": normalize_digits,
}


def normalize_value(fact_type: str, value: str) -> str:
    fn = NORMALIZERS.get(fact_type, normalize_generic)
    return fn(value)
