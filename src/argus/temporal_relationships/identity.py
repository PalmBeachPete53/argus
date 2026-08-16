"""Phase 6 — deterministic identity of a Temporal Relationship (legacy name ``reaction_id``).

A ``temporal_relationship_id`` is derived solely from the relationship itself —
the central bank and the two source change ids — so the *same pair of changes
observed the same way always yields the same id*. It is reproducible,
self-explanatory and stable across rebuilds (idempotent persistence), and never
"invented": both sides are real ``FactChange`` objects.

The persisted id value is unchanged from the legacy ``reaction_id``: the payload
is identical, only the function name is canonicalized.
"""

from __future__ import annotations

import hashlib


def temporal_relationship_id_of(
    *,
    central_bank: str | None,
    earlier_change_id: str,
    later_change_id: str,
) -> str:
    """Deterministic id of the relationship ``earlier change → later change``.

    The payload is the central bank and the two source change ids, joined by
    unit separators so no combination can collide.
    """
    payload = "\x1f".join(
        (central_bank or "", earlier_change_id, later_change_id)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def reaction_id_of(
    *,
    central_bank: str | None,
    condition_change_id: str,
    policy_change_id: str,
) -> str:
    """Legacy name for :func:`temporal_relationship_id_of` (identical payload,
    identical result)."""
    return temporal_relationship_id_of(
        central_bank=central_bank,
        earlier_change_id=condition_change_id,
        later_change_id=policy_change_id,
    )
