"""Application service for the intake pipeline.

The access guard runs at the start of every public method. Extraction
may write candidates and flags only. Ready for attorney is a staff
action, and only after the quality gate returns no reasons.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from intake.access import AccessContext
from intake.checklist import current_version, set_current_version
from intake.constants import (
    ACCOUNTED_DOC_STATUSES,
    ADMIN,
    ATTORNEY,
    BOUNDARY_NOTICE,
    CHANNELS,
    DEMO_PASSWORD,
    DISPOSITIONS,
    DOC_EXTRACTED,
    DOC_HELD_OUT,
    DOC_INCOMPLETE,
    DOC_REGISTERED,
    DOC_UNPROCESSED,
    DOCUMENT_TYPES,
    FACT_APPROVED,
    FACT_CANDIDATE,
    FACT_LABELS,
    FACT_REJECTED,
    FACT_SUPERSEDED,
    FLAG_CONFLICT,
    FLAG_INCOMPLETE,
    FLAG_MISSING,
    FORBIDDEN_LABELS,
    LABEL_APPROVED,
    LABEL_CANDIDATE,
    LABEL_SUPPLIED,
    MAX_BUNDLE,
    MAX_DOCUMENTS,
    MIN_BUNDLE,
    OUTBOUND_MESSAGE_COUNT,
    RELEASED_STATUSES,
    STAFF,
    STATUS_ATTORNEY,
    STATUS_CORRECTED,
    STATUS_EXTRACTED,
    STATUS_IN_REVIEW,
    STATUS_READY,
    STATUS_REGISTERED,
    STATUS_RETURNED,
    STATUSES,
    TEMPLATE_VERSION,
    TEXT_EXTENSIONS,
)
from intake.db import connect, init_schema
from intake.errors import AccessDenied, AuthRequired, GateFailed, ValidationError
from intake.extractors import get_extractor
from intake.flags import build_flags
from intake.gate import assess
from intake.masking import SSN_RE
from intake.normalize import normalize_value
from intake.prohibited import find_prohibited
from intake.report import compose
from intake.tools import assert_allowed, invoke

SEED_USERS = (
    ("casey.lin", "Casey Lin", STAFF),
    ("morgan.ellis", "Morgan Ellis", ADMIN),
    ("riley.brooks", "Riley Brooks", ATTORNEY),
    ("sam.patel", "Sam Patel", STAFF),
)

TRANSITIONS = {
    STATUS_REGISTERED: {STATUS_EXTRACTED},
    STATUS_EXTRACTED: {STATUS_IN_REVIEW},
    STATUS_IN_REVIEW: {STATUS_CORRECTED, STATUS_READY},
    STATUS_CORRECTED: {STATUS_READY},
    STATUS_READY: {STATUS_ATTORNEY, STATUS_RETURNED},
    STATUS_ATTORNEY: {STATUS_RETURNED},
    STATUS_RETURNED: {STATUS_IN_REVIEW, STATUS_READY},
}

EDITABLE = {
    STATUS_EXTRACTED,
    STATUS_IN_REVIEW,
    STATUS_CORRECTED,
    STATUS_RETURNED,
}

READY_FROM = {
    STATUS_EXTRACTED,
    STATUS_IN_REVIEW,
    STATUS_CORRECTED,
    STATUS_RETURNED,
}

TYPE_HINTS = (
    ("intake", "intake form"),
    ("incident", "incident description"),
    ("witness", "incident description"),
    ("medical", "medical note"),
    ("clinic", "medical note"),
    ("email", "email"),
    ("court", "court filing"),
    ("insurer", "insurer communication"),
)

MAX_BYTES = 1_000_000
ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = ROOT / "samples"


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return salt.hex() + ":" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split(":", 1)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), 120_000
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def infer_document_type(filename: str, hint: str | None) -> str:
    if hint and hint.strip():
        wanted = hint.strip().casefold()
        for item in DOCUMENT_TYPES:
            if item.casefold() == wanted:
                return item
        raise ValidationError(
            "Unknown document type. Use one of: " + ", ".join(DOCUMENT_TYPES)
        )
    name = filename.casefold()
    for key, label in TYPE_HINTS:
        if key in name:
            return label
    return "Other"


class IntakeService:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.conn = connect(self.db_path)
        self._lock = threading.RLock()

    def init_db(self) -> None:
        with self._lock:
            init_schema(self.conn)
            for user_id, display, role in SEED_USERS:
                existing = self.conn.execute(
                    "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
                ).fetchone()
                if existing:
                    continue
                self.conn.execute(
                    """
                    INSERT INTO users (user_id, display_name, role, password_hash)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, display, role, hash_password(DEMO_PASSWORD)),
                )
            self.conn.commit()

    # ----- identity and access -------------------------------------------------

    def authenticate(self, user_id: str, password: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT user_id, display_name, role, password_hash FROM users WHERE user_id = ?",
                (user_id.strip(),),
            ).fetchone()
            if row is None or not verify_password(password, row["password_hash"]):
                return None
            return {
                "user_id": row["user_id"],
                "display_name": row["display_name"],
                "role": row["role"],
            }

    def _now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _check_identity(self, ctx: AccessContext | None) -> dict:
        if ctx is None or not ctx.user_id or not ctx.role:
            raise AuthRequired("Sign in is required")
        row = self.conn.execute(
            "SELECT user_id, display_name, role FROM users WHERE user_id = ?",
            (ctx.user_id,),
        ).fetchone()
        if row is None:
            raise AccessDenied("Unknown user")
        if row["role"] != ctx.role:
            raise AccessDenied("Role does not match this user")
        return dict(row)

    def _member(self, user_id: str, matter_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM matter_access WHERE matter_id = ? AND user_id = ?",
            (matter_id, user_id),
        ).fetchone()
        return row is not None

    def _can_read(self, ctx: AccessContext, matter_id: str) -> bool:
        self._check_identity(ctx)
        if ctx.matter_id and ctx.matter_id != matter_id:
            return False
        return self._member(ctx.user_id, matter_id)

    def _guard(self, ctx: AccessContext, matter_id: str, roles: tuple[str, ...]) -> None:
        self._check_identity(ctx)
        if ctx.role not in roles:
            raise AccessDenied("This role cannot do that")
        if ctx.matter_id and ctx.matter_id != matter_id:
            raise AccessDenied("Access context matter does not match this call")
        if not self._member(ctx.user_id, matter_id):
            raise AccessDenied("No access to this matter")

    def _next_id(self, counter: str, prefix: str) -> str:
        row = self.conn.execute(
            "SELECT value FROM counters WHERE name = ?", (counter,)
        ).fetchone()
        if row is None:
            value = 1
            self.conn.execute(
                "INSERT INTO counters (name, value) VALUES (?, ?)", (counter, value)
            )
        else:
            value = row["value"] + 1
            self.conn.execute(
                "UPDATE counters SET value = ? WHERE name = ?", (value, counter)
            )
        return f"{prefix}-{value:04d}"

    def _audit(self, ctx: AccessContext, matter_id: str, action: str, object_id: str | None, detail) -> None:
        assert_allowed("audit-logger", "1.0.0")
        if not isinstance(detail, str):
            detail = json.dumps(detail, sort_keys=True)
        now = self._now()
        self.conn.execute(
            """
            INSERT INTO audit_log (matter_id, user_id, role, action, object_id, detail, at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (matter_id, ctx.user_id, ctx.role, action, object_id, detail, now),
        )
        self.conn.execute(
            """
            INSERT INTO tool_log (matter_id, user_id, role, tool_name, tool_version, at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (matter_id, ctx.user_id, ctx.role, "audit-logger", "1.0.0", now),
        )

    def _log_tool(self, ctx: AccessContext, matter_id: str, name: str, version: str) -> None:
        assert_allowed(name, version)
        self.conn.execute(
            """
            INSERT INTO tool_log (matter_id, user_id, role, tool_name, tool_version, at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (matter_id, ctx.user_id, ctx.role, name, version, self._now()),
        )

    def _stage(self, matter_id: str, stage: str) -> None:
        now = self._now()
        self.conn.execute(
            "UPDATE matters SET processing_stage = ? WHERE matter_id = ?",
            (stage, matter_id),
        )
        self.conn.execute(
            "INSERT INTO stage_events (matter_id, stage, at) VALUES (?, ?, ?)",
            (matter_id, stage, now),
        )

    def _matter_row(self, matter_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM matters WHERE matter_id = ?", (matter_id,)
        ).fetchone()
        return dict(row) if row else None

    def _require_matter(self, matter_id: str) -> dict:
        matter = self._matter_row(matter_id)
        if matter is None:
            raise ValidationError("Matter not found")
        return matter

    def _set_status(self, ctx: AccessContext, matter_id: str, new_status: str, detail: str) -> None:
        if new_status not in STATUSES:
            raise ValidationError(f"Illegal status '{new_status}'")
        matter = self._require_matter(matter_id)
        allowed = TRANSITIONS.get(matter["status"], set())
        if new_status not in allowed:
            raise ValidationError(
                f"Cannot move from '{matter['status']}' to '{new_status}'"
            )
        self.conn.execute(
            "UPDATE matters SET status = ? WHERE matter_id = ?",
            (new_status, matter_id),
        )
        self._audit(ctx, matter_id, "status_changed", matter_id, {
            "from": matter["status"],
            "to": new_status,
            "detail": detail,
        })

    def _grant_raw(self, matter_id: str, user_id: str) -> None:
        row = self.conn.execute(
            "SELECT role FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise ValidationError("Unknown user")
        self.conn.execute(
            """
            INSERT OR IGNORE INTO matter_access (matter_id, user_id, role)
            VALUES (?, ?, ?)
            """,
            (matter_id, user_id, row["role"]),
        )

    # ----- read models ---------------------------------------------------------

    def list_matters(self, ctx: AccessContext) -> list[dict]:
        with self._lock:
            self._check_identity(ctx)
            rows = self.conn.execute(
                """
                SELECT m.* FROM matters m
                JOIN matter_access a ON a.matter_id = m.matter_id
                WHERE a.user_id = ?
                ORDER BY m.created_at, m.matter_id
                """,
                (ctx.user_id,),
            ).fetchall()
            matters = [dict(row) for row in rows]
            if ctx.matter_id:
                matters = [item for item in matters if item["matter_id"] == ctx.matter_id]
            return matters

    def get_matter(self, ctx: AccessContext, matter_id: str) -> dict | None:
        with self._lock:
            if not self._can_read(ctx, matter_id):
                return None
            return self._matter_row(matter_id)

    def list_documents(self, ctx: AccessContext, matter_id: str) -> list[dict]:
        with self._lock:
            if not self._can_read(ctx, matter_id):
                return []
            rows = self.conn.execute(
                """
                SELECT document_id, matter_id, lineage_id, filename, document_type,
                       channel, received_at, extracted_at, length_bytes, version,
                       locator, label, status, reason, is_current
                FROM documents
                WHERE matter_id = ?
                ORDER BY filename, version
                """,
                (matter_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_facts(self, ctx: AccessContext, matter_id: str, partition: str = "working") -> list[dict]:
        with self._lock:
            if not self._can_read(ctx, matter_id):
                return []
            if ctx.role == ATTORNEY:
                matter = self._matter_row(matter_id)
                if matter is None or matter["status"] not in RELEASED_STATUSES:
                    return []
                partition = "approved"
            clauses = {
                "candidate": "active = 1 AND status = ?",
                "approved": "active = 1 AND status = ?",
                "working": "active = 1 AND status IN (?, ?)",
                "history": "active = 0 OR status IN (?, ?)",
            }
            params: list = [matter_id]
            if partition == "candidate":
                sql = clauses["candidate"]
                params.append(FACT_CANDIDATE)
            elif partition == "approved":
                sql = clauses["approved"]
                params.append(FACT_APPROVED)
            elif partition == "history":
                sql = clauses["history"]
                params.extend([FACT_REJECTED, FACT_SUPERSEDED])
            else:
                sql = clauses["working"]
                params.extend([FACT_CANDIDATE, FACT_APPROVED])
            rows = self.conn.execute(
                f"""
                SELECT * FROM facts
                WHERE matter_id = ? AND ({sql})
                ORDER BY fact_type, fact_id
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def get_fact(self, ctx: AccessContext, matter_id: str, fact_id: str) -> dict | None:
        with self._lock:
            if not self._can_read(ctx, matter_id):
                return None
            row = self.conn.execute(
                "SELECT * FROM facts WHERE matter_id = ? AND fact_id = ?",
                (matter_id, fact_id),
            ).fetchone()
            if row is None:
                return None
            fact = dict(row)
            if ctx.role == ATTORNEY and fact["status"] != FACT_APPROVED:
                return None
            return fact

    def list_flags(self, ctx: AccessContext, matter_id: str) -> list[dict]:
        with self._lock:
            if not self._can_read(ctx, matter_id):
                return []
            if ctx.role == ATTORNEY:
                matter = self._matter_row(matter_id)
                if matter is None or matter["status"] not in RELEASED_STATUSES:
                    return []
            return self._flags(matter_id, active_only=False)

    def list_quotes(self, ctx: AccessContext, matter_id: str) -> list[dict]:
        with self._lock:
            if not self._can_read(ctx, matter_id):
                return []
            if ctx.role == ATTORNEY:
                matter = self._matter_row(matter_id)
                if matter is None or matter["status"] not in RELEASED_STATUSES:
                    return []
            current = {
                row["document_id"]
                for row in self.conn.execute(
                    "SELECT document_id FROM documents WHERE matter_id = ? AND is_current = 1",
                    (matter_id,),
                ).fetchall()
            }
            rows = self.conn.execute(
                "SELECT * FROM quotations WHERE matter_id = ? ORDER BY quote_id",
                (matter_id,),
            ).fetchall()
            return [dict(row) for row in rows if row["document_id"] in current]

    def list_audit(self, ctx: AccessContext, matter_id: str) -> list[dict]:
        with self._lock:
            if not self._can_read(ctx, matter_id):
                return []
            rows = self.conn.execute(
                """
                SELECT audit_id, matter_id, user_id, role, action, object_id, detail, at
                FROM audit_log
                WHERE matter_id = ?
                ORDER BY audit_id
                """,
                (matter_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_stages(self, ctx: AccessContext, matter_id: str) -> list[dict]:
        with self._lock:
            if not self._can_read(ctx, matter_id):
                return []
            rows = self.conn.execute(
                "SELECT stage, at FROM stage_events WHERE matter_id = ? ORDER BY id",
                (matter_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_decisions(self, ctx: AccessContext, matter_id: str) -> list[dict]:
        with self._lock:
            if not self._can_read(ctx, matter_id):
                return []
            rows = self.conn.execute(
                "SELECT * FROM review_decisions WHERE matter_id = ? ORDER BY decision_id",
                (matter_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def open_source(self, ctx: AccessContext, matter_id: str, document_id: str) -> dict | None:
        with self._lock:
            if not self._can_read(ctx, matter_id):
                return None
            matter = self._matter_row(matter_id)
            if ctx.role == ATTORNEY and (
                matter is None or matter["status"] not in RELEASED_STATUSES
            ):
                return None
            row = self.conn.execute(
                """
                SELECT document_id, matter_id, filename, version, locator, label,
                       status, content, text_content
                FROM documents
                WHERE matter_id = ? AND document_id = ?
                """,
                (matter_id, document_id),
            ).fetchone()
            if row is None:
                return None
            self._audit(ctx, matter_id, "source_opened", document_id, {
                "version": row["version"],
                "locator": row["locator"],
            })
            self.conn.commit()
            return dict(row)

    def outbound_message_count(self, ctx: AccessContext, matter_id: str) -> int:
        """Always zero. The pipeline has no outbound channel. FR-17."""
        with self._lock:
            if not self._can_read(ctx, matter_id):
                return 0
            return OUTBOUND_MESSAGE_COUNT

    # ----- registration and extraction -----------------------------------------

    def register_bundle(
        self,
        ctx: AccessContext,
        title: str,
        files: list,
        extract_now: bool = True,
        sample_key: str | None = None,
    ) -> str:
        with self._lock:
            try:
                matter_id = self._register_bundle(ctx, title, files, extract_now, sample_key)
                self.conn.commit()
                return matter_id
            except Exception:
                self.conn.rollback()
                raise

    def _register_bundle(self, ctx, title, files, extract_now, sample_key) -> str:
        self._check_identity(ctx)
        if ctx.role != STAFF:
            raise AccessDenied("Only intake staff can register a bundle")
        if ctx.matter_id:
            raise AccessDenied("Registration cannot run under another matter's context")
        if not title or not title.strip():
            raise ValidationError("A matter title is required")
        if not (MIN_BUNDLE <= len(files) <= MAX_BUNDLE):
            raise ValidationError(
                f"Register a bundle of {MIN_BUNDLE} to {MAX_BUNDLE} files. "
                f"This request had {len(files)}."
            )
        matter_id = self._next_id("matter", "M")
        now = self._now()
        self.conn.execute(
            """
            INSERT INTO matters (
                matter_id, title, status, checklist_version, template_version,
                created_at, created_by, processing_stage, sample_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                matter_id,
                title.strip(),
                STATUS_REGISTERED,
                current_version(),
                TEMPLATE_VERSION,
                now,
                ctx.user_id,
                "registering",
                sample_key,
            ),
        )
        self._grant_raw(matter_id, ctx.user_id)
        bound = AccessContext(ctx.user_id, ctx.role, matter_id)
        self._stage(matter_id, "registering")
        self._audit(bound, matter_id, "matter_created", matter_id, {"title": title.strip()})
        for incoming in files:
            self._store_file(bound, matter_id, incoming)
        if extract_now:
            self._finish_extraction(bound, matter_id)
        return matter_id

    def extract(self, ctx: AccessContext, matter_id: str) -> str:
        """Extract candidates from registered files. Cannot mark a matter ready."""
        with self._lock:
            try:
                self._guard(ctx, matter_id, (STAFF,))
                self._finish_extraction(ctx, matter_id)
                self.conn.commit()
                return self._require_matter(matter_id)["status"]
            except Exception:
                self.conn.rollback()
                raise

    def add_document(self, ctx: AccessContext, matter_id: str, incoming, extract_now: bool = True) -> str:
        with self._lock:
            try:
                document_id = self._add_document(ctx, matter_id, incoming, extract_now)
                self.conn.commit()
                return document_id
            except Exception:
                self.conn.rollback()
                raise

    def _add_document(self, ctx, matter_id, incoming, extract_now) -> str:
        self._guard(ctx, matter_id, (STAFF,))
        current = self.conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE matter_id = ? AND is_current = 1",
            (matter_id,),
        ).fetchone()["n"]
        # A new version of an existing filename does not grow the bundle count.
        filename = Path(incoming.filename).name
        exists = self.conn.execute(
            """
            SELECT 1 FROM documents
            WHERE matter_id = ? AND filename = ? AND is_current = 1
            """,
            (matter_id, filename),
        ).fetchone()
        if not exists and current >= MAX_DOCUMENTS:
            raise ValidationError(
                f"This prototype holds at most {MAX_DOCUMENTS} current documents."
            )
        document_id = self._store_file(ctx, matter_id, incoming)
        if extract_now:
            self._finish_extraction(ctx, matter_id)
        return document_id

    def _store_file(self, ctx: AccessContext, matter_id: str, incoming) -> str:
        filename = Path(getattr(incoming, "filename", "") or "").name
        content = getattr(incoming, "content", b"") or b""
        if not filename:
            raise ValidationError("A filename is required")
        if len(content) > MAX_BYTES:
            raise ValidationError("File is too large for this prototype")
        channel = getattr(incoming, "channel", None) or "upload"
        if channel not in CHANNELS:
            raise ValidationError("Unknown channel. Use phone, web form, email, walk-in, or upload.")
        document_type = infer_document_type(filename, getattr(incoming, "document_type", None))
        status, reason, text = self._classify(filename, content, matter_id)
        previous = self.conn.execute(
            """
            SELECT document_id, lineage_id, version
            FROM documents
            WHERE matter_id = ? AND filename = ? AND is_current = 1
            """,
            (matter_id, filename),
        ).fetchone()
        if previous:
            lineage = previous["lineage_id"]
            version = previous["version"] + 1
            self.conn.execute(
                "UPDATE documents SET is_current = 0 WHERE document_id = ?",
                (previous["document_id"],),
            )
            changed = self.conn.execute(
                """
                UPDATE facts
                SET active = 0,
                    status = CASE WHEN status = ? THEN ? ELSE status END
                WHERE document_id = ? AND active = 1
                """,
                (FACT_CANDIDATE, FACT_SUPERSEDED, previous["document_id"]),
            )
            self._audit(ctx, matter_id, "version_added", previous["document_id"], {
                "filename": filename,
                "new_version": version,
                "facts_deactivated": changed.rowcount,
            })
            matter = self._matter_row(matter_id)
            if matter and matter["status"] in (STATUS_READY, STATUS_ATTORNEY):
                self._set_status(
                    ctx,
                    matter_id,
                    STATUS_RETURNED,
                    "A new document version arrived after release",
                )
                self.conn.execute(
                    "UPDATE matters SET return_reason = ? WHERE matter_id = ?",
                    ("A new document version arrived after release", matter_id),
                )
        else:
            version = 1
            lineage = None
        document_id = self._next_id("document", "D")
        if lineage is None:
            lineage = document_id
        locator = f"{document_id}/v{version}"
        now = self._now()
        self._log_tool(ctx, matter_id, "document-store", "1.0.0")
        self.conn.execute(
            """
            INSERT INTO documents (
                document_id, matter_id, lineage_id, filename, document_type, channel,
                received_at, extracted_at, length_bytes, version, locator, label,
                status, reason, content, text_content, is_current
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                document_id,
                matter_id,
                lineage,
                filename,
                document_type,
                channel,
                now,
                len(content),
                version,
                locator,
                LABEL_SUPPLIED,
                status,
                reason,
                content,
                text,
            ),
        )
        self._audit(ctx, matter_id, "document_registered", document_id, {
            "filename": filename,
            "document_type": document_type,
            "channel": channel,
            "version": version,
            "length_bytes": len(content),
            "status": status,
            "reason": reason,
            "label": LABEL_SUPPLIED,
        })
        if status != DOC_REGISTERED:
            self._audit(ctx, matter_id, "document_excepted", document_id, {
                "status": status,
                "reason": reason,
            })
        return document_id

    def _classify(self, filename: str, content: bytes, matter_id: str):
        suffix = Path(filename).suffix.casefold()
        if suffix not in TEXT_EXTENSIONS or b"\x00" in content:
            return (
                DOC_UNPROCESSED,
                "unsupported file type; only .txt and .md are read in this prototype",
                None,
            )
        text = content.decode("utf-8", errors="replace")
        match = re.search(r"(?im)^matter-binding:\s*(\S+)\s*$", text)
        if match and match.group(1) != matter_id:
            return (
                DOC_HELD_OUT,
                f"matter mismatch: file is bound to {match.group(1)}",
                text,
            )
        return DOC_REGISTERED, None, text

    def _finish_extraction(self, ctx: AccessContext, matter_id: str) -> None:
        self._extract_pending(ctx, matter_id)
        matter = self._require_matter(matter_id)
        pending = self.conn.execute(
            """
            SELECT COUNT(*) AS n FROM documents
            WHERE matter_id = ? AND is_current = 1 AND status = ?
            """,
            (matter_id, DOC_REGISTERED),
        ).fetchone()["n"]
        if pending == 0 and matter["status"] == STATUS_REGISTERED:
            self._set_status(ctx, matter_id, STATUS_EXTRACTED, "extraction finished; candidates only")

    def _extract_pending(self, ctx: AccessContext, matter_id: str) -> None:
        # Candidates only. This method has no path that sets Ready.
        self._stage(matter_id, "extracting")
        extractor = get_extractor()
        rows = self.conn.execute(
            """
            SELECT document_id, text_content
            FROM documents
            WHERE matter_id = ? AND is_current = 1 AND status = ?
            """,
            (matter_id, DOC_REGISTERED),
        ).fetchall()
        for row in rows:
            text = row["text_content"] or ""
            document_id = row["document_id"]

            def _run(text=text):
                return extractor.extract(text)

            output = invoke(extractor.name, extractor.version, _run)
            self._log_tool(ctx, matter_id, extractor.name, extractor.version)
            extracted_at = self._now()
            if output.unreadable:
                self.conn.execute(
                    """
                    UPDATE documents
                    SET status = ?, reason = ?, extracted_at = ?
                    WHERE document_id = ?
                    """,
                    (DOC_INCOMPLETE, output.reason, extracted_at, document_id),
                )
                self._audit(ctx, matter_id, "extraction_incomplete", document_id, {
                    "reason": output.reason,
                    "fact_count": 0,
                })
                continue
            self.conn.execute(
                """
                UPDATE documents
                SET status = ?, extracted_at = ?, reason = NULL
                WHERE document_id = ?
                """,
                (DOC_EXTRACTED, extracted_at, document_id),
            )
            version = self.conn.execute(
                "SELECT version FROM documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()["version"]
            for item in output.facts:
                if item.excerpt not in text:
                    continue
                if item.fact_type not in FACT_LABELS:
                    continue
                fact_id = self._next_id("fact", "F")
                sensitive = 1 if item.fact_type in ("government_id", "account_number") or SSN_RE.search(item.value) else 0
                locator = f"{document_id}/v{version}/p1/L{item.line_no}"
                self.conn.execute(
                    """
                    INSERT INTO facts (
                        fact_id, matter_id, fact_type, value, normalized_value,
                        document_id, version, locator, excerpt, status, confidence,
                        label, sensitive, active, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        fact_id,
                        matter_id,
                        item.fact_type,
                        item.value,
                        normalize_value(item.fact_type, item.value),
                        document_id,
                        version,
                        locator,
                        item.excerpt,
                        FACT_CANDIDATE,
                        item.confidence,
                        LABEL_CANDIDATE,
                        sensitive,
                        extracted_at,
                    ),
                )
                self._audit(ctx, matter_id, "candidate_written", fact_id, {
                    "fact_type": item.fact_type,
                    "document_id": document_id,
                    "locator": locator,
                    "status": FACT_CANDIDATE,
                    "label": LABEL_CANDIDATE,
                })
            for quote in output.quotes:
                if quote.excerpt not in text:
                    continue
                quote_id = self._next_id("quote", "Q")
                locator = f"{document_id}/v{version}/p1/L{quote.line_no}"
                self.conn.execute(
                    """
                    INSERT INTO quotations (
                        quote_id, matter_id, document_id, locator, excerpt, label
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (quote_id, matter_id, document_id, locator, quote.excerpt, LABEL_SUPPLIED),
                )
        self._stage(matter_id, "flagging")
        self._recompute_flags(ctx, matter_id)

    def _recompute_flags(self, ctx: AccessContext, matter_id: str) -> None:
        fact_rows = self.conn.execute(
            """
            SELECT * FROM facts
            WHERE matter_id = ? AND active = 1 AND status IN (?, ?)
            """,
            (matter_id, FACT_CANDIDATE, FACT_APPROVED),
        ).fetchall()
        documents = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT document_id, filename, status, is_current
                FROM documents WHERE matter_id = ?
                """,
                (matter_id,),
            ).fetchall()
        ]
        problems = [
            {
                "document_id": doc["document_id"],
                "filename": doc["filename"],
                "reason": self.conn.execute(
                    "SELECT reason FROM documents WHERE document_id = ?",
                    (doc["document_id"],),
                ).fetchone()["reason"],
            }
            for doc in documents
            if doc["is_current"] and doc["status"] == DOC_INCOMPLETE
        ]
        drafts = build_flags([dict(row) for row in fact_rows], documents, problems)
        existing = [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM flags WHERE matter_id = ? AND active = 1",
                (matter_id,),
            ).fetchall()
        ]
        by_sig = {row["signature"]: row for row in existing}
        seen = set()
        now = self._now()
        for draft in drafts:
            seen.add(draft["signature"])
            if draft["signature"] in by_sig:
                continue
            flag_id = self._next_id("flag", "G")
            self.conn.execute(
                """
                INSERT INTO flags (
                    flag_id, matter_id, flag_type, fact_type, checklist_key,
                    blocks_ready, gap_reason, display_fact_id, active, signature,
                    document_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    flag_id,
                    matter_id,
                    draft["flag_type"],
                    draft["fact_type"],
                    draft["checklist_key"],
                    draft["blocks_ready"],
                    draft["gap_reason"],
                    draft["display_fact_id"],
                    draft["signature"],
                    draft["document_id"],
                    now,
                ),
            )
            for member in draft["members"]:
                self.conn.execute(
                    """
                    INSERT INTO flag_members (
                        flag_id, fact_id, value, normalized_value, document_id, locator, excerpt
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        flag_id,
                        member.get("fact_id"),
                        member.get("value") or "",
                        member.get("normalized_value"),
                        member.get("document_id"),
                        member.get("locator"),
                        member.get("excerpt"),
                    ),
                )
            self._audit(ctx, matter_id, "flag_opened", flag_id, {
                "flag_type": draft["flag_type"],
                "fact_type": draft["fact_type"],
                "blocks_ready": draft["blocks_ready"],
                "display_fact_id": draft["display_fact_id"],
            })
        for row in existing:
            if row["signature"] not in seen:
                self.conn.execute(
                    "UPDATE flags SET active = 0 WHERE flag_id = ?",
                    (row["flag_id"],),
                )
                self._audit(ctx, matter_id, "flag_superseded", row["flag_id"], {
                    "flag_type": row["flag_type"],
                    "kept_in_history": True,
                })
        self._return_if_blocking(ctx, matter_id)

    def _return_if_blocking(self, ctx: AccessContext, matter_id: str) -> None:
        matter = self._require_matter(matter_id)
        if matter["status"] not in (STATUS_READY, STATUS_ATTORNEY):
            return
        open_block = self.conn.execute(
            """
            SELECT COUNT(*) AS n FROM flags
            WHERE matter_id = ? AND active = 1 AND blocks_ready = 1
              AND disposition IS NULL
            """,
            (matter_id,),
        ).fetchone()["n"]
        if open_block:
            self._set_status(
                ctx,
                matter_id,
                STATUS_RETURNED,
                "A new blocking flag arrived after release",
            )
            self.conn.execute(
                "UPDATE matters SET return_reason = ? WHERE matter_id = ?",
                ("A new blocking flag arrived after release", matter_id),
            )

    def _flags(self, matter_id: str, active_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM flags WHERE matter_id = ?"
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY flag_id"
        flags = []
        for row in self.conn.execute(sql, (matter_id,)).fetchall():
            flag = dict(row)
            members = self.conn.execute(
                """
                SELECT fact_id, value, normalized_value, document_id, locator, excerpt
                FROM flag_members WHERE flag_id = ?
                ORDER BY value
                """,
                (flag["flag_id"],),
            ).fetchall()
            flag["members"] = [dict(member) for member in members]
            # Conflicts do not carry a winner. The key is absent on purpose.
            flag.pop("winning_value", None)
            flags.append(flag)
        return flags

    # ----- review --------------------------------------------------------------

    def begin_review(self, ctx: AccessContext, matter_id: str) -> str:
        with self._lock:
            try:
                self._guard(ctx, matter_id, (STAFF,))
                matter = self._require_matter(matter_id)
                if matter["status"] in (STATUS_EXTRACTED, STATUS_RETURNED):
                    self._set_status(ctx, matter_id, STATUS_IN_REVIEW, "staff opened the review")
                self.conn.commit()
                return self._require_matter(matter_id)["status"]
            except Exception:
                self.conn.rollback()
                raise

    def _require_editable(self, ctx: AccessContext, matter_id: str) -> dict:
        self._guard(ctx, matter_id, (STAFF,))
        matter = self._require_matter(matter_id)
        if matter["status"] not in EDITABLE:
            raise ValidationError(
                f"Facts cannot be changed while the status is '{matter['status']}'"
            )
        return matter

    def _note_staff_edit(self, ctx: AccessContext, matter_id: str) -> None:
        matter = self._require_matter(matter_id)
        if matter["status"] == STATUS_EXTRACTED:
            self._set_status(ctx, matter_id, STATUS_IN_REVIEW, "staff started corrections")
            matter = self._require_matter(matter_id)
        if matter["status"] == STATUS_RETURNED:
            self._set_status(ctx, matter_id, STATUS_IN_REVIEW, "staff resumed after return")
            matter = self._require_matter(matter_id)
        if matter["status"] == STATUS_IN_REVIEW:
            self._set_status(ctx, matter_id, STATUS_CORRECTED, "staff corrected a candidate")

    def _decision(self, ctx: AccessContext, matter_id: str, object_id: str, action: str, reason: str) -> None:
        decision_id = self._next_id("decision", "R")
        self.conn.execute(
            """
            INSERT INTO review_decisions (
                decision_id, matter_id, object_id, action, actor_id, acted_at, reason, role
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (decision_id, matter_id, object_id, action, ctx.user_id, self._now(), reason, ctx.role),
        )

    def _require_reason(self, reason: str) -> str:
        if reason is None or not str(reason).strip():
            raise ValidationError("A reason is required")
        return str(reason).strip()

    def _reject_prohibited(self, value: str) -> None:
        hits = find_prohibited(value)
        if hits:
            raise ValidationError(
                "That value reads as a legal conclusion ("
                + ", ".join(hits)
                + "). It was not stored as an approved fact."
            )

    def _working_fact(self, matter_id: str, fact_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM facts WHERE matter_id = ? AND fact_id = ?",
            (matter_id, fact_id),
        ).fetchone()
        if row is None or not row["active"]:
            raise ValidationError("Fact is not in the working set")
        if row["status"] not in (FACT_CANDIDATE, FACT_APPROVED):
            raise ValidationError("Fact cannot be changed")
        if row["label"] in FORBIDDEN_LABELS:
            raise ValidationError("Forbidden label")
        return dict(row)

    def accept_fact(self, ctx: AccessContext, matter_id: str, fact_id: str, reason: str) -> None:
        with self._lock:
            try:
                self._accept(ctx, matter_id, fact_id, reason)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def accept_facts(self, ctx: AccessContext, matter_id: str, fact_ids: list[str], reason: str) -> None:
        with self._lock:
            try:
                for fact_id in fact_ids:
                    self._accept(ctx, matter_id, fact_id, reason)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def _accept(self, ctx, matter_id, fact_id, reason) -> None:
        reason = self._require_reason(reason)
        self._require_editable(ctx, matter_id)
        fact = self._working_fact(matter_id, fact_id)
        self._reject_prohibited(fact["value"])
        now = self._now()
        self.conn.execute(
            """
            UPDATE facts
            SET status = ?, label = ?, actor_id = ?, acted_at = ?, reason = ?
            WHERE fact_id = ?
            """,
            (FACT_APPROVED, LABEL_APPROVED, ctx.user_id, now, reason, fact_id),
        )
        self._decision(ctx, matter_id, fact_id, "accept", reason)
        self._audit(ctx, matter_id, "fact_accepted", fact_id, {
            "actor": ctx.user_id,
            "at": now,
            "reason": reason,
            "value": fact["value"],
            "status": FACT_APPROVED,
            "label": LABEL_APPROVED,
        })
        self._note_staff_edit(ctx, matter_id)

    def edit_fact(self, ctx: AccessContext, matter_id: str, fact_id: str, new_value: str, reason: str) -> None:
        with self._lock:
            try:
                reason = self._require_reason(reason)
                if not new_value or not new_value.strip():
                    raise ValidationError("A replacement value is required")
                new_value = new_value.strip()
                self._reject_prohibited(new_value)
                self._require_editable(ctx, matter_id)
                fact = self._working_fact(matter_id, fact_id)
                now = self._now()
                self.conn.execute(
                    """
                    UPDATE facts
                    SET value = ?, normalized_value = ?, status = ?, label = ?,
                        prior_value = ?, actor_id = ?, acted_at = ?, reason = ?,
                        sensitive = ?
                    WHERE fact_id = ?
                    """,
                    (
                        new_value,
                        normalize_value(fact["fact_type"], new_value),
                        FACT_APPROVED,
                        LABEL_APPROVED,
                        fact["value"],
                        ctx.user_id,
                        now,
                        reason,
                        1 if fact["fact_type"] in ("government_id", "account_number") or SSN_RE.search(new_value) else 0,
                        fact_id,
                    ),
                )
                self._decision(ctx, matter_id, fact_id, "edit", reason)
                self._audit(ctx, matter_id, "fact_edited", fact_id, {
                    "prior_value": fact["value"],
                    "new_value": new_value,
                    "actor": ctx.user_id,
                    "at": now,
                    "reason": reason,
                })
                self._recompute_flags(ctx, matter_id)
                self._note_staff_edit(ctx, matter_id)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def reject_fact(self, ctx: AccessContext, matter_id: str, fact_id: str, reason: str) -> None:
        with self._lock:
            try:
                reason = self._require_reason(reason)
                self._require_editable(ctx, matter_id)
                fact = self._working_fact(matter_id, fact_id)
                now = self._now()
                self.conn.execute(
                    """
                    UPDATE facts
                    SET status = ?, active = 0, actor_id = ?, acted_at = ?, reason = ?
                    WHERE fact_id = ?
                    """,
                    (FACT_REJECTED, ctx.user_id, now, reason, fact_id),
                )
                self._decision(ctx, matter_id, fact_id, "reject", reason)
                self._audit(ctx, matter_id, "fact_rejected", fact_id, {
                    "value": fact["value"],
                    "actor": ctx.user_id,
                    "at": now,
                    "reason": reason,
                    "retained_in_history": True,
                })
                self._recompute_flags(ctx, matter_id)
                self._note_staff_edit(ctx, matter_id)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def split_fact(self, ctx: AccessContext, matter_id: str, fact_id: str, values: list[str], reason: str) -> list[str]:
        with self._lock:
            try:
                reason = self._require_reason(reason)
                cleaned = [value.strip() for value in values if value and value.strip()]
                if len(cleaned) < 2:
                    raise ValidationError("A split needs two values")
                self._require_editable(ctx, matter_id)
                fact = self._working_fact(matter_id, fact_id)
                for value in cleaned:
                    self._reject_prohibited(value)
                    if value.casefold() not in fact["excerpt"].casefold():
                        raise ValidationError(
                            f"'{value}' is not in the source excerpt, so it was not added"
                        )
                now = self._now()
                self.conn.execute(
                    """
                    UPDATE facts
                    SET status = ?, active = 0, actor_id = ?, acted_at = ?, reason = ?
                    WHERE fact_id = ?
                    """,
                    (FACT_REJECTED, ctx.user_id, now, reason, fact_id),
                )
                new_ids = []
                for value in cleaned:
                    new_id = self._next_id("fact", "F")
                    new_ids.append(new_id)
                    self.conn.execute(
                        """
                        INSERT INTO facts (
                            fact_id, matter_id, fact_type, value, normalized_value,
                            document_id, version, locator, excerpt, status, confidence,
                            label, sensitive, active, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (
                            new_id,
                            matter_id,
                            fact["fact_type"],
                            value,
                            normalize_value(fact["fact_type"], value),
                            fact["document_id"],
                            fact["version"],
                            fact["locator"],
                            fact["excerpt"],
                            FACT_CANDIDATE,
                            fact["confidence"],
                            LABEL_CANDIDATE,
                            fact["sensitive"],
                            now,
                        ),
                    )
                self._decision(ctx, matter_id, fact_id, "split", reason)
                self._audit(ctx, matter_id, "fact_split", fact_id, {
                    "prior_value": fact["value"],
                    "new_fact_ids": new_ids,
                    "values": cleaned,
                    "actor": ctx.user_id,
                    "at": now,
                    "reason": reason,
                })
                self._recompute_flags(ctx, matter_id)
                self._note_staff_edit(ctx, matter_id)
                self.conn.commit()
                return new_ids
            except Exception:
                self.conn.rollback()
                raise

    def dispose_flag(self, ctx: AccessContext, matter_id: str, flag_id: str, disposition: str, reason: str) -> None:
        with self._lock:
            try:
                reason = self._require_reason(reason)
                if disposition not in DISPOSITIONS:
                    raise ValidationError(
                        "Disposition must be one of: " + ", ".join(DISPOSITIONS)
                    )
                self._require_editable(ctx, matter_id)
                row = self.conn.execute(
                    "SELECT * FROM flags WHERE matter_id = ? AND flag_id = ? AND active = 1",
                    (matter_id, flag_id),
                ).fetchone()
                if row is None:
                    raise ValidationError("Flag not found")
                now = self._now()
                self.conn.execute(
                    """
                    UPDATE flags
                    SET disposition = ?, disposition_actor = ?, disposition_at = ?,
                        disposition_reason = ?
                    WHERE flag_id = ?
                    """,
                    (disposition, ctx.user_id, now, reason, flag_id),
                )
                self._decision(ctx, matter_id, flag_id, "dispose", reason)
                self._audit(ctx, matter_id, "flag_disposed", flag_id, {
                    "disposition": disposition,
                    "actor": ctx.user_id,
                    "at": now,
                    "reason": reason,
                    "flag_type": row["flag_type"],
                })
                self._note_staff_edit(ctx, matter_id)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def set_duplicate_display(self, ctx: AccessContext, matter_id: str, flag_id: str, fact_id: str, reason: str) -> None:
        with self._lock:
            try:
                reason = self._require_reason(reason)
                self._require_editable(ctx, matter_id)
                flag = self.conn.execute(
                    "SELECT * FROM flags WHERE flag_id = ? AND matter_id = ? AND active = 1",
                    (flag_id, matter_id),
                ).fetchone()
                if flag is None or flag["flag_type"] != "Duplicate":
                    raise ValidationError("That flag is not an active duplicate")
                member = self.conn.execute(
                    "SELECT 1 FROM flag_members WHERE flag_id = ? AND fact_id = ?",
                    (flag_id, fact_id),
                ).fetchone()
                if member is None:
                    raise ValidationError("That fact is not in the duplicate group")
                self.conn.execute(
                    "UPDATE flags SET display_fact_id = ? WHERE flag_id = ?",
                    (fact_id, flag_id),
                )
                self._audit(ctx, matter_id, "duplicate_display_set", flag_id, {
                    "fact_id": fact_id,
                    "reason": reason,
                    "actor": ctx.user_id,
                })
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    # ----- gate, report, attorney ----------------------------------------------

    def _snapshot(self, ctx: AccessContext, matter_id: str) -> dict:
        matter = self._require_matter(matter_id)
        documents = self.list_documents(AccessContext(ctx.user_id, ctx.role, matter_id), matter_id)
        # list_documents takes the lock. Caller already holds RLock.
        approved = [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM facts WHERE matter_id = ? AND active = 1 AND status = ?",
                (matter_id, FACT_APPROVED),
            ).fetchall()
        ]
        texts = {
            row["document_id"]: row["text_content"] or ""
            for row in self.conn.execute(
                "SELECT document_id, text_content FROM documents WHERE matter_id = ?",
                (matter_id,),
            ).fetchall()
        }
        for fact in approved:
            fact["excerpt_in_source"] = fact["excerpt"] in texts.get(fact["document_id"], "")
        flags = self._flags(matter_id, active_only=False)
        report = self._compose_unlocked(ctx, matter_id, "attorney")
        return {
            "role": ctx.role,
            "user_id": ctx.user_id,
            "documents": documents,
            "approved_facts": approved,
            "flags": flags,
            "scan_text": report["scan_text"],
            "notice_text": report["text"],
        }

    def evaluate_gate(self, ctx: AccessContext, matter_id: str) -> dict:
        with self._lock:
            try:
                if not self._can_read(ctx, matter_id):
                    raise AccessDenied("No access to this matter")
                reasons = assess(self._snapshot(ctx, matter_id))
                self.conn.commit()
                return {"passed": not reasons, "reasons": reasons}
            except Exception:
                self.conn.rollback()
                raise

    def mark_ready(self, ctx: AccessContext, matter_id: str, reason: str) -> dict:
        with self._lock:
            try:
                reason = self._require_reason(reason)
                self._guard(ctx, matter_id, (STAFF,))
                matter = self._require_matter(matter_id)
                if matter["status"] not in READY_FROM:
                    raise ValidationError(
                        f"Cannot mark ready from '{matter['status']}'"
                    )
                if matter["status"] == STATUS_EXTRACTED:
                    self._set_status(ctx, matter_id, STATUS_IN_REVIEW, "staff requested ready")
                reasons = assess(self._snapshot(ctx, matter_id))
                if reasons:
                    raise GateFailed(reasons)
                now = self._now()
                self._set_status(ctx, matter_id, STATUS_READY, reason)
                self.conn.execute(
                    "UPDATE matters SET ready_by = ?, ready_at = ? WHERE matter_id = ?",
                    (ctx.user_id, now, matter_id),
                )
                self._decision(ctx, matter_id, matter_id, "mark_ready", reason)
                self.conn.commit()
                return {"status": STATUS_READY, "ready_by": ctx.user_id, "ready_at": now}
            except Exception:
                self.conn.rollback()
                raise

    def _open_questions(self, matter_id: str) -> list[str]:
        questions = []
        docs = {
            row["document_id"]: row["filename"]
            for row in self.conn.execute(
                "SELECT document_id, filename FROM documents WHERE matter_id = ?",
                (matter_id,),
            ).fetchall()
        }
        for flag in self._flags(matter_id, active_only=True):
            if flag.get("disposition"):
                continue
            if flag["flag_type"] == FLAG_MISSING:
                label = (flag.get("checklist_key") or flag["fact_type"]).replace("_", " ")
                questions.append(
                    f"What is the {label}? It is not in the registered documents, and no value was filled in."
                )
            elif flag["flag_type"] == FLAG_INCOMPLETE:
                filename = docs.get(flag.get("document_id"), "a document")
                questions.append(
                    f"What does '{filename}' say? The passage could not be read, and nothing was invented."
                )
            elif flag["flag_type"] == FLAG_CONFLICT:
                values = [member["value"] for member in flag["members"]]
                label = FACT_LABELS.get(flag["fact_type"], flag["fact_type"])
                questions.append(
                    f"{label} has more than one value ({'; '.join(values)}). Which value should the file use?"
                )
        return questions

    def _compose_unlocked(self, ctx: AccessContext, matter_id: str, audience: str) -> dict:
        matter = self._require_matter(matter_id)
        if audience == "attorney" and ctx.role == ATTORNEY and matter["status"] not in RELEASED_STATUSES:
            raise AccessDenied("The report is not released yet")
        if audience not in ("attorney", "staff"):
            audience = "attorney"
        if ctx.role == ATTORNEY:
            audience = "attorney"
        documents = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT document_id, filename, document_type, channel, version, status,
                       reason, is_current, label
                FROM documents WHERE matter_id = ?
                ORDER BY filename, version
                """,
                (matter_id,),
            ).fetchall()
        ]
        approved = [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM facts WHERE matter_id = ? AND active = 1 AND status = ?",
                (matter_id, FACT_APPROVED),
            ).fetchall()
        ]
        candidates = [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM facts WHERE matter_id = ? AND active = 1 AND status = ?",
                (matter_id, FACT_CANDIDATE),
            ).fetchall()
        ]
        current_ids = {doc["document_id"] for doc in documents if doc["is_current"]}
        quotes = [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM quotations WHERE matter_id = ?",
                (matter_id,),
            ).fetchall()
            if row["document_id"] in current_ids
        ]
        self._stage(matter_id, "composing")
        self._log_tool(ctx, matter_id, "report-composer", "1.0.0")
        return compose(
            {
                "matter": matter,
                "documents": documents,
                "approved_facts": approved,
                "candidates": candidates,
                "flags": self._flags(matter_id, active_only=True),
                "quotes": quotes,
                "open_questions": self._open_questions(matter_id),
                "audience": audience,
            }
        )

    def compose_report(self, ctx: AccessContext, matter_id: str, audience: str = "staff") -> dict:
        with self._lock:
            try:
                if not self._can_read(ctx, matter_id):
                    raise AccessDenied("No access to this matter")
                report = self._compose_unlocked(ctx, matter_id, audience)
                # Composition must not move the review status.
                self.conn.commit()
                return report
            except Exception:
                self.conn.rollback()
                raise

    def acknowledge(self, ctx: AccessContext, matter_id: str) -> str:
        with self._lock:
            try:
                self._guard(ctx, matter_id, (ATTORNEY,))
                matter = self._require_matter(matter_id)
                if matter["status"] != STATUS_READY:
                    raise ValidationError("The attorney can acknowledge only a released report")
                before = [
                    (row["fact_id"], row["status"], row["value"])
                    for row in self.conn.execute(
                        "SELECT fact_id, status, value FROM facts WHERE matter_id = ?",
                        (matter_id,),
                    ).fetchall()
                ]
                now = self._now()
                self._set_status(ctx, matter_id, STATUS_ATTORNEY, "attorney acknowledgment only")
                self.conn.execute(
                    """
                    UPDATE matters
                    SET attorney_ack_by = ?, attorney_ack_at = ?
                    WHERE matter_id = ?
                    """,
                    (ctx.user_id, now, matter_id),
                )
                self._decision(ctx, matter_id, matter_id, "acknowledge", "seen the report")
                after = [
                    (row["fact_id"], row["status"], row["value"])
                    for row in self.conn.execute(
                        "SELECT fact_id, status, value FROM facts WHERE matter_id = ?",
                        (matter_id,),
                    ).fetchall()
                ]
                if before != after:
                    raise ValidationError("Acknowledgment must not change facts")
                self.conn.commit()
                return STATUS_ATTORNEY
            except Exception:
                self.conn.rollback()
                raise

    def return_report(self, ctx: AccessContext, matter_id: str, reason: str) -> str:
        with self._lock:
            try:
                reason = self._require_reason(reason)
                self._guard(ctx, matter_id, (ATTORNEY,))
                matter = self._require_matter(matter_id)
                if matter["status"] not in (STATUS_READY, STATUS_ATTORNEY):
                    raise ValidationError("There is no released report to return")
                self.conn.execute(
                    "UPDATE matters SET return_reason = ? WHERE matter_id = ?",
                    (reason, matter_id),
                )
                self._set_status(ctx, matter_id, STATUS_RETURNED, reason)
                self._decision(ctx, matter_id, matter_id, "return", reason)
                self.conn.commit()
                return STATUS_RETURNED
            except Exception:
                self.conn.rollback()
                raise

    def reveal(self, ctx: AccessContext, matter_id: str, fact_id: str, reason: str) -> str:
        with self._lock:
            try:
                reason = self._require_reason(reason)
                self._guard(ctx, matter_id, (STAFF, ADMIN))
                row = self.conn.execute(
                    "SELECT * FROM facts WHERE matter_id = ? AND fact_id = ?",
                    (matter_id, fact_id),
                ).fetchone()
                if row is None:
                    raise ValidationError("Fact not found")
                now = self._now()
                self.conn.execute(
                    """
                    INSERT INTO reveals (matter_id, fact_id, actor_id, role, reason, at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (matter_id, fact_id, ctx.user_id, ctx.role, reason, now),
                )
                self._audit(ctx, matter_id, "value_revealed", fact_id, {
                    "actor": ctx.user_id,
                    "at": now,
                    "reason": reason,
                    "fact_type": row["fact_type"],
                })
                self.conn.commit()
                return row["value"]
            except Exception:
                self.conn.rollback()
                raise

    def ask(self, ctx: AccessContext, matter_id: str, question: str) -> str:
        with self._lock:
            try:
                self._guard(ctx, matter_id, (STAFF, ADMIN, ATTORNEY))
                before = self.conn.execute(
                    "SELECT COUNT(*) AS n FROM facts WHERE matter_id = ?",
                    (matter_id,),
                ).fetchone()["n"]
                hits = find_prohibited(question or "")
                if hits:
                    self._audit(ctx, matter_id, "prohibited_question_refused", None, {
                        "hits": hits,
                    })
                    self.conn.commit()
                    return (
                        "That question asks for a legal conclusion. This pipeline will not "
                        "answer it. Take it to the attorney outside the pipeline."
                    )
                self._audit(ctx, matter_id, "freeform_question_refused", None, {"stored_as_fact": False})
                after = self.conn.execute(
                    "SELECT COUNT(*) AS n FROM facts WHERE matter_id = ?",
                    (matter_id,),
                ).fetchone()["n"]
                if before != after:
                    raise ValidationError("A question must not create a fact")
                self.conn.commit()
                return (
                    "This pipeline does not answer free-form questions. "
                    "Use the checklist, the flags, and the approved facts."
                )
            except Exception:
                self.conn.rollback()
                raise

    def grant_access(self, ctx: AccessContext, matter_id: str, user_id: str) -> None:
        with self._lock:
            try:
                self._guard(ctx, matter_id, (STAFF, ADMIN))
                self._grant_raw(matter_id, user_id)
                self._audit(ctx, matter_id, "access_granted", user_id, {"user_id": user_id})
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def set_checklist_version(self, ctx: AccessContext, version: str) -> str:
        """Change the version used by later matters. Existing matters stay pinned."""
        with self._lock:
            self._check_identity(ctx)
            if ctx.role != ADMIN:
                raise AccessDenied("Only an office administrator can publish a checklist version")
            set_current_version(version)
            return current_version()

    def invoke_named_tool(self, ctx: AccessContext, matter_id: str, name: str, version: str, callback, *args):
        """Run an allow-listed tool. Refused tools never receive the callback arguments."""
        with self._lock:
            try:
                self._guard(ctx, matter_id, (STAFF, ADMIN, ATTORNEY))
                result = invoke(name, version, callback, *args)
                self._log_tool(ctx, matter_id, name, version)
                self.conn.commit()
                return result
            except Exception:
                self.conn.rollback()
                raise

    def load_samples(self, ctx: AccessContext) -> list[dict]:
        with self._lock:
            try:
                created = self._load_samples(ctx)
                self.conn.commit()
                return created
            except Exception:
                self.conn.rollback()
                raise

    def _load_samples(self, ctx: AccessContext) -> list[dict]:
        self._check_identity(ctx)
        if ctx.role != STAFF:
            raise AccessDenied("Only intake staff can load the sample bundles")
        if ctx.matter_id:
            raise AccessDenied("Load samples without a matter context")
        loaded = []
        for manifest_path in sorted(SAMPLE_ROOT.glob("*/manifest.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing = self.conn.execute(
                "SELECT matter_id, title FROM matters WHERE sample_key = ?",
                (manifest["key"],),
            ).fetchone()
            if existing:
                loaded.append({"matter_id": existing["matter_id"], "title": existing["title"], "key": manifest["key"]})
                continue
            files = []
            for item in manifest["files"]:
                content = (manifest_path.parent / item["name"]).read_bytes()
                files.append(
                    _Incoming(item["name"], content, item.get("type"), item.get("channel") or "upload")
                )
            staff_ctx = AccessContext(ctx.user_id, ctx.role, None)
            matter_id = self._register_bundle(
                staff_ctx,
                manifest["title"],
                files,
                extract_now=True,
                sample_key=manifest["key"],
            )
            for user_id in ("morgan.ellis", "riley.brooks"):
                self._grant_raw(matter_id, user_id)
                self._audit(staff_ctx, matter_id, "access_granted", user_id, {"user_id": user_id})
            loaded.append({"matter_id": matter_id, "title": manifest["title"], "key": manifest["key"]})
        return loaded


class IncomingFile:
    """One file in a bundle. `content` is the original bytes."""

    def __init__(self, filename, content, document_type=None, channel="upload"):
        self.filename = filename
        self.content = content
        self.document_type = document_type
        self.channel = channel


_Incoming = IncomingFile
