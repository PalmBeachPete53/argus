from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

PUBLICATION_TYPES = (
    "monetary_policy_decision",
    "monetary_policy_statement",
    "press_conference",
    "minutes",
    "meeting_account",
    "economic_projections",
    "monetary_policy_report",
    "speech",
    "interview",
    "other",
    "unknown",
)

# Canonical mapping from adapter ``Source.publication_types`` values (and any
# free-form hint) onto the canonical vocabulary above.
HINT_CANONICAL = {
    "monetary_policy_decision": "monetary_policy_decision",
    "policy_interest_rate": "monetary_policy_decision",
    "monetary_policy_statement": "monetary_policy_statement",
    "monetary_policy_assessment": "monetary_policy_statement",
    "statement_on_monetary_policy": "monetary_policy_report",
    "press_conference": "press_conference",
    "minutes": "minutes",
    "mpc_minutes": "minutes",
    "meeting_account": "meeting_account",
    "accounts": "meeting_account",
    "projections": "economic_projections",
    "forecast": "economic_projections",
    "forecasts": "economic_projections",
    "economic_projections": "economic_projections",
    "monetary_policy_report": "monetary_policy_report",
    "speech": "speech",
    "interview": "interview",
    "press_release": "other",
}

# Classification methods — the tier that decided the classification.
METHOD_SOURCE_TYPE_HINT = "source_type_hint"
METHOD_URL_PATTERN = "url_pattern"
METHOD_TITLE_PATTERN = "title_pattern"
METHOD_DOCUMENT_METADATA = "document_metadata"
METHOD_CONTENT_HEURISTIC = "content_heuristic"
METHOD_UNRESOLVED = "unresolved"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class PublicationClassification:
    publication_id: str
    central_bank: str
    publication_type: str
    confidence: Confidence
    method: str
    evidence: list[str] = field(default_factory=list)
    classified_at: datetime | None = None
    publication_title: str | None = None
    source_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "publication_id": self.publication_id,
            "central_bank": self.central_bank,
            "publication_type": self.publication_type,
            "confidence": self.confidence.value,
            "method": self.method,
            "evidence": list(self.evidence),
            "classified_at": self.classified_at.isoformat() if self.classified_at else None,
            "publication_title": self.publication_title,
            "source_id": self.source_id,
        }