"""Candidate extraction.

The default extractor is a fixed set of line rules. It needs no key and
it never marks a fact approved. A remote language model is optional and
stays behind the same interface. Set LLM_PROVIDER=remote to use it.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field

from intake.constants import FACT_TYPES
from intake.errors import ValidationError

UNREADABLE_MARK = "[[UNREADABLE]]"


@dataclass
class ExtractedFact:
    fact_type: str
    value: str
    line_no: int
    excerpt: str
    confidence: float


@dataclass
class ExtractedQuote:
    line_no: int
    excerpt: str


@dataclass
class ExtractionOutput:
    facts: list[ExtractedFact] = field(default_factory=list)
    quotes: list[ExtractedQuote] = field(default_factory=list)
    unreadable: bool = False
    reason: str | None = None


LINE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("client_name", re.compile(r"(?i)^client(?: name)?\s*:\s*(.+)$")),
    ("client_contact", re.compile(r"(?i)^(?:contact|phone|email)\s*:\s*(.+)$")),
    ("incident_date", re.compile(r"(?i)^(?:incident date|date of incident)\s*:\s*(.+)$")),
    ("incident_time", re.compile(r"(?i)^(?:incident time|time of incident)\s*:\s*(.+)$")),
    ("incident_location", re.compile(r"(?i)^(?:incident location|location)\s*:\s*(.+)$")),
    ("parties", re.compile(r"(?i)^parties\s*:\s*(.+)$")),
    ("party", re.compile(r"(?i)^party\s*:\s*(.+)$")),
    ("injury_or_treatment", re.compile(r"(?i)^(?:injury(?: or treatment)?|treatment)\s*:\s*(.+)$")),
    ("insurer", re.compile(r"(?i)^(?:insurer|insurance company)\s*:\s*(.+)$")),
    ("document_date", re.compile(r"(?i)^(?:document date|date of document)\s*:\s*(.+)$")),
    ("government_id", re.compile(r"(?i)^(?:ssn|government id)\s*:\s*(.+)$")),
    ("account_number", re.compile(r"(?i)^(?:account(?: number)?)\s*:\s*(.+)$")),
    ("quote", re.compile(r"(?i)^(?:quote|source statement)\s*:\s*(.+)$")),
]

FAULT_LINE = re.compile(r"(?i)\b(at fault|liable|negligen)")
SKIP_LINE = re.compile(r"(?i)^(matter-binding\s*:|#)")
ILLEGIBLE = re.compile(r"(?i)^(illegible|unclear)\b")
LOW_VALUE = re.compile(r"(?i)^(unclear|unknown|\[illegible\])\b")


def looks_unreadable(text: str) -> bool:
    if UNREADABLE_MARK in text:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    if len(stripped) >= 30:
        letters = sum(ch.isalnum() for ch in stripped)
        if letters / len(stripped) < 0.30:
            return True
    return False


def _facts_from_text(text: str) -> ExtractionOutput:
    output = ExtractionOutput()
    if looks_unreadable(text):
        output.unreadable = True
        output.reason = "Text could not be read. No facts were invented."
        return output

    illegible = False
    for index, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or SKIP_LINE.match(line):
            continue
        if ILLEGIBLE.match(line):
            illegible = True
            continue
        matched = False
        for kind, pattern in LINE_RULES:
            found = pattern.match(line)
            if not found:
                continue
            matched = True
            value = found.group(1).strip()
            if LOW_VALUE.match(value):
                illegible = True
                break
            if kind == "quote":
                output.quotes.append(ExtractedQuote(index, line))
                break
            if kind == "parties":
                parts = [part.strip() for part in value.split(";") if part.strip()]
                for part in parts:
                    output.facts.append(
                        ExtractedFact("party", part, index, line, 0.9)
                    )
                break
            output.facts.append(ExtractedFact(kind, value, index, line, 0.9))
            break
        if not matched and FAULT_LINE.search(line):
            # Keep fault language as a supplied quotation, not as a fact.
            output.quotes.append(ExtractedQuote(index, line))

    if illegible:
        output.unreadable = True
        output.facts = []
        output.reason = "A passage was illegible. No facts were invented for it."
    return output


class FixtureExtractor:
    """Rule-based extractor. Versioned and on the tool allow list."""

    name = "fixture-extractor"
    version = "1.0.0"

    def extract(self, text: str) -> ExtractionOutput:
        return _facts_from_text(text)


def parse_model_payload(source_text: str, payload: dict) -> ExtractionOutput:
    """Turn a model JSON object into candidates.

    Facts whose excerpt is not in the source are dropped. An "approved"
    field from the model is ignored. Status is assigned later, by staff.
    """
    output = ExtractionOutput()
    if not isinstance(payload, dict):
        output.unreadable = True
        output.reason = "The model reply was not a JSON object."
        return output
    if payload.get("unreadable"):
        output.unreadable = True
        output.reason = "The model reported that the text could not be read."
        return output
    for item in payload.get("facts") or []:
        if not isinstance(item, dict):
            continue
        fact_type = str(item.get("fact_type") or "")
        value = str(item.get("value") or "").strip()
        excerpt = str(item.get("excerpt") or "").strip()
        if fact_type not in FACT_TYPES or not value or not excerpt:
            continue
        if excerpt not in source_text:
            continue
        line_no = source_text[: source_text.find(excerpt)].count("\n") + 1
        output.facts.append(ExtractedFact(fact_type, value, line_no, excerpt, 0.6))
    for item in payload.get("quotes") or []:
        excerpt = item if isinstance(item, str) else str((item or {}).get("excerpt") or "")
        excerpt = excerpt.strip()
        if excerpt and excerpt in source_text:
            line_no = source_text[: source_text.find(excerpt)].count("\n") + 1
            output.quotes.append(ExtractedQuote(line_no, excerpt))
    return output


class RemoteExtractor:
    """Optional HTTP extractor. Used only when LLM_PROVIDER=remote."""

    name = "remote-language-model"
    version = "1.0.0"

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    @classmethod
    def from_env(cls) -> "RemoteExtractor":
        base = os.environ.get("LLM_BASE_URL", "").strip()
        key = os.environ.get("LLM_API_KEY", "").strip()
        model = os.environ.get("LLM_MODEL", "").strip()
        if not base or not key or not model:
            raise ValidationError(
                "LLM_PROVIDER=remote requires LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL."
            )
        return cls(base, key, model)

    def extract(self, text: str) -> ExtractionOutput:
        prompt = (
            "Extract intake facts from the document. Reply with JSON only: "
            '{"facts": [{"fact_type": "", "value": "", "excerpt": ""}], '
            '"quotes": [{"excerpt": ""}], "unreadable": false}. '
            "fact_type must be one of: "
            + ", ".join(FACT_TYPES)
            + ". excerpt must be copied from the document. "
            "Do not decide fault, value a claim, or approve anything.\n\n"
            + text
        )
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode()
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = json.loads(response.read().decode())
        content = raw["choices"][0]["message"]["content"]
        start = content.find("{")
        end = content.rfind("}")
        payload = json.loads(content[start : end + 1]) if start >= 0 else {}
        return parse_model_payload(text, payload)


def load_local_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_extractor():
    """Return the fixture extractor unless a remote provider was requested."""
    load_local_env()
    provider = os.environ.get("LLM_PROVIDER", "fixture").strip().lower() or "fixture"
    if provider == "fixture":
        return FixtureExtractor()
    if provider == "remote":
        return RemoteExtractor.from_env()
    raise ValidationError(f"Unknown LLM_PROVIDER '{provider}'. Use fixture or remote.")
