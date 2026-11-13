"""Shared labels, roles, and the locked report notice."""

STAFF = "intake_staff"
ADMIN = "office_administrator"
ATTORNEY = "attorney_reviewer"
ROLES = (STAFF, ADMIN, ATTORNEY)

# Review-status lifecycle from FR-15. No other value is accepted.
STATUS_REGISTERED = "Documents registered"
STATUS_EXTRACTED = "Candidates extracted"
STATUS_IN_REVIEW = "Staff review in progress"
STATUS_CORRECTED = "Corrections applied"
STATUS_READY = "Ready for attorney"
STATUS_RETURNED = "Returned for revision"
STATUS_ATTORNEY = "Attorney reviewed"

STATUSES = (
    STATUS_REGISTERED,
    STATUS_EXTRACTED,
    STATUS_IN_REVIEW,
    STATUS_CORRECTED,
    STATUS_READY,
    STATUS_RETURNED,
    STATUS_ATTORNEY,
)

# Statuses from which staff may still change facts.
EDITABLE_STATUSES = (
    STATUS_EXTRACTED,
    STATUS_IN_REVIEW,
    STATUS_CORRECTED,
    STATUS_RETURNED,
)

READY_FROM = (
    STATUS_EXTRACTED,
    STATUS_IN_REVIEW,
    STATUS_CORRECTED,
    STATUS_RETURNED,
)

RELEASED_STATUSES = (
    STATUS_READY,
    STATUS_RETURNED,
    STATUS_ATTORNEY,
)

LABEL_SUPPLIED = "Supplied"
LABEL_CANDIDATE = "Generated-Candidate"
LABEL_APPROVED = "Approved"

# These two labels are never written. FR-05.
FORBIDDEN_LABELS = ("Verified Evidence", "Attorney Conclusion")

FACT_CANDIDATE = "Candidate"
FACT_APPROVED = "Approved"
FACT_REJECTED = "Rejected"
FACT_SUPERSEDED = "Superseded"

DOC_REGISTERED = "registered"
DOC_EXTRACTED = "extracted"
DOC_UNPROCESSED = "unprocessed"
DOC_HELD_OUT = "held_out"
DOC_INCOMPLETE = "extraction_incomplete"

ACCOUNTED_DOC_STATUSES = (
    DOC_EXTRACTED,
    DOC_UNPROCESSED,
    DOC_HELD_OUT,
    DOC_INCOMPLETE,
)

FLAG_MISSING = "Missing"
FLAG_DUPLICATE = "Duplicate"
FLAG_CONFLICT = "Conflict"
FLAG_INCOMPLETE = "Extraction incomplete"

DISPOSITIONS = (
    "Accepted as-is",
    "Resolved by correction",
    "Escalated to attorney",
    "Deferred",
)

DOCUMENT_TYPES = (
    "intake form",
    "incident description",
    "medical note",
    "email",
    "court filing",
    "insurer communication",
    "Other",
)

CHANNELS = ("phone", "web form", "email", "walk-in", "upload")

TEXT_EXTENSIONS = (".txt", ".md", ".text")

# Unique inside one matter. Two different normalized values are a conflict.
UNIQUE_FACT_TYPES = (
    "client_name",
    "client_contact",
    "incident_date",
    "incident_time",
    "incident_location",
    "insurer",
    "government_id",
    "account_number",
)

# More than one value is normal. Only identical values form a duplicate.
MULTI_FACT_TYPES = (
    "party",
    "injury_or_treatment",
    "document_date",
)

FACT_TYPES = UNIQUE_FACT_TYPES + MULTI_FACT_TYPES

FACT_LABELS = {
    "client_name": "Client name",
    "client_contact": "Client contact",
    "incident_date": "Incident date",
    "incident_time": "Incident time",
    "incident_location": "Incident location",
    "party": "Party",
    "injury_or_treatment": "Injury or treatment",
    "insurer": "Insurer",
    "document_date": "Document date",
    "government_id": "Government ID",
    "account_number": "Account number",
}

# Checklist from the design baseline. Identity and contact are one item.
CHECKLIST = (
    {
        "key": "identity_and_contact",
        "label": "identity and contact",
        "requires": ("client_name", "client_contact"),
    },
    {
        "key": "incident_date",
        "label": "incident date",
        "requires": ("incident_date",),
    },
    {
        "key": "incident_time",
        "label": "incident time",
        "requires": ("incident_time",),
    },
    {
        "key": "incident_location",
        "label": "incident location",
        "requires": ("incident_location",),
    },
    {
        "key": "parties",
        "label": "parties",
        "requires": ("party",),
    },
    {
        "key": "injury_or_treatment",
        "label": "injury or treatment",
        "requires": ("injury_or_treatment",),
    },
    {
        "key": "insurer",
        "label": "insurer",
        "requires": ("insurer",),
    },
    {
        "key": "document_dates",
        "label": "document dates",
        "requires": ("document_date",),
    },
)

CHECKLIST_VERSION = "intake-checklist-1.0"
TEMPLATE_VERSION = "report-template-1.0"

# NFR-20. The template has no switch that removes this sentence.
BOUNDARY_NOTICE = (
    "Candidate extractions are not verified evidence until staff approval. "
    "This pipeline does not provide legal advice. "
    "Attorneys retain professional judgment."
)

# Tools that may see matter text. Anything else is refused. NFR-12.
ALLOWED_TOOLS = {
    "document-store": "1.0.0",
    "fixture-extractor": "1.0.0",
    "remote-language-model": "1.0.0",
    "report-composer": "1.0.0",
    "audit-logger": "1.0.0",
}

DEMO_PASSWORD = "intake-demo"
MIN_BUNDLE = 2
MAX_BUNDLE = 4
MAX_DOCUMENTS = 6

# There is no outbound channel. FR-17.
OUTBOUND_MESSAGE_COUNT = 0

PROHIBITED_PATTERNS = (
    (r"\bliab(?:le|ility)\b", "liability"),
    (r"\bnegligen(?:t|ce)\b", "negligence"),
    (r"\bat fault\b", "fault finding"),
    (r"\bclaim validity\b", "claim validity"),
    (r"\bvalid claim\b", "claim validity"),
    (r"\bcase value\b", "case value"),
    (r"\brecommended legal action\b", "recommended legal action"),
    (r"\brecommend filing\b", "recommended legal action"),
    (r"\brepresentation advice\b", "representation advice"),
    (r"\baccept representation\b", "representation advice"),
    (r"\bwho is liable\b", "liability question"),
    (r"\bshould (?:we|the office) take\b", "representation question"),
)
