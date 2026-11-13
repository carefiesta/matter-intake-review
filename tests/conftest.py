"""Shared fixtures. The extractor stays on the local rules unless a test says otherwise."""

import pytest

from intake.access import AccessContext
from intake.constants import ADMIN, ATTORNEY, STAFF
from intake.service import IncomingFile, IntakeService


@pytest.fixture(autouse=True)
def local_extractor(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fixture")


@pytest.fixture
def svc(tmp_path):
    service = IntakeService(tmp_path / "intake.db")
    service.init_db()
    return service


@pytest.fixture
def staff():
    return AccessContext("casey.lin", STAFF, None)


@pytest.fixture
def admin():
    return AccessContext("morgan.ellis", ADMIN, None)


@pytest.fixture
def attorney():
    return AccessContext("riley.brooks", ATTORNEY, None)


def bind(ctx: AccessContext, matter_id: str) -> AccessContext:
    return AccessContext(ctx.user_id, ctx.role, matter_id)


def file_of(name: str, body: str, document_type: str | None = None, channel: str = "upload") -> IncomingFile:
    return IncomingFile(name, body.encode("utf-8"), document_type, channel)


CLEAN_A = """
Document date: March 6, 2026
Client: Jordan Hale
Contact: (555) 010-1001
Incident date: March 4, 2026
Incident time: 2:15 p.m.
Incident location: Palm Avenue and 3rd Street
Parties: Jordan Hale; Avery Ortiz
Injury: neck strain
Insurer: Northline Mutual
""".strip()

CLEAN_B = """
Document date: March 7, 2026
Client: Jordan Hale
Incident date: 03/04/2026
Incident time: 14:15
Incident location: Palm Avenue and 3rd Street
Parties: Jordan Hale; Avery Ortiz
Injury: neck strain
Insurer: Northline Mutual
""".strip()

CONFLICT_A = """
Document date: March 8, 2026
Client: Minh Nguyen
Contact: (555) 010-2002
Incident date: March 3, 2026
Incident location: Lot C, Harbor Market
Parties: Minh Nguyen; Parker Cole
Injury: bruised knee
""".strip()

CONFLICT_B = """
Document date: March 9, 2026
Client: Minh Nguyen
Incident date: March 5, 2026
Incident location: Lot C, Harbor Market
Parties: Minh Nguyen; Parker Cole
Injury: bruised knee
""".strip()


def clean_bundle():
    return [
        file_of("01-intake-form.txt", CLEAN_A, "intake form", "web form"),
        file_of("02-incident-note.txt", CLEAN_B, "incident description", "email"),
    ]


def conflict_bundle():
    return [
        file_of("01-intake-form.txt", CONFLICT_A, "intake form", "walk-in"),
        file_of("02-witness-note.txt", CONFLICT_B, "incident description", "email"),
    ]
