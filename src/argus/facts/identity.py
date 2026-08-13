"""Deterministic Fact identity.

The identity payload is built from **stable semantic + provenance fields** only:

- publication_id
- document_id
- subject
- predicate
- period (canonical form)
- effective_date (ISO form)

The extracted ``value``, ``previous_value`` and ``change`` are deliberately
**not** part of the identity: a corrected extraction updates the same row
instead of creating a duplicate. ``qualifier`` is an optional discriminator for
the rare case where two facts share subject + predicate + period +
effective_date in one document.

``effective_date`` IS part of the identity because it is a stable, semantic
attribute — two facts in the same document that differ only by their effective
date (e.g. a rate set for two different dates) are genuinely distinct facts and
must not collide. The value corpus remains excluded, so value corrections still
update the row in place. A *corrected* ``effective_date`` opens a new slot; the
full re-extraction path (`rebuild_facts_for_document` / deletes) clears stale
rows.

Re-running an extractor (same inputs) yields the same ``fact_id``, which makes
persistence idempotent by construction.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from .base import FactPeriod


def fact_id_of(
    *,
    publication_id: str,
    document_id: str,
    subject: str,
    predicate: str,
    period: FactPeriod | None = None,
    effective_date: datetime | None = None,
    qualifier: str = "",
) -> str:
    period_part = period.canonical() if period is not None else ""
    effective_part = effective_date.isoformat() if effective_date is not None else ""
    payload = "\x1f".join(
        [
            publication_id,
            document_id,
            subject,
            predicate,
            period_part,
            effective_part,
            qualifier,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()