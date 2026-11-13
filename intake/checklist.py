"""Checklist version pin.

New matters copy the current version. Changing it does not rewrite a
matter that was already registered. NFR-16.
"""

from __future__ import annotations

from intake.constants import CHECKLIST_VERSION

_version = CHECKLIST_VERSION


def current_version() -> str:
    return _version


def set_current_version(version: str) -> None:
    global _version
    if not version or not version.strip():
        raise ValueError("Checklist version is required")
    _version = version.strip()
