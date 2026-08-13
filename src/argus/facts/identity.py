"""Deterministic Fact identity.

The identity payload is built from **stable semantic + provenance fields** only:

- publication_id
- document_id
- subject
- predicate
- period (canonical form)

The extracted ``value``, ``previous_value`` and ``change`` are deliberately
**not** part of the identity: a corrected extraction updates the same row
instead of creating a duplicate. ``qualifier`` is an optional discriminator for
the rare case where two facts share subject + predicate + period in one document.

Re-running an extractor (same inputs) yields the same ``fact_id``, which makes
persistence idempotent by construction.
"""

from __future__ import annotations

import hashlib

from .base import FactPeriod


def fact_id_of(
    *,
    publication_id: str,
    document_id: str,
    subject: str,
    predicate: str,
    period: FactPeriod | None = None,
    qualifier: str = "",
) -> str:
    period_part = period.canonical() if period is not None else ""
    payload = "\x1f".join(
        [
            publication_id,
            document_id,
            subject,
            predicate,
            period_part,
            qualifier,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()