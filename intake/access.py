"""Access context carried on every read and write. FR-21."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AccessContext:
    user_id: str
    role: str
    matter_id: str | None = None
