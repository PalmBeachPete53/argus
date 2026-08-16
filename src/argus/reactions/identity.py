"""Phase 6 — deterministic identity of a ``PolicyReaction``.

A ``reaction_id`` is derived solely from the relationship itself — the central
bank and the two source change ids — so the *same pair of changes observed the
same way always yields the same id*. It is reproducible, self-explanatory and
stable across rebuilds (idempotent persistence), and never "invented": both
sides are real ``FactChange`` objects.
"""

from __future__ import annotations

import hashlib


def reaction_id_of(
    *,
    central_bank: str | None,
    condition_change_id: str,
    policy_change_id: str,
) -> str:
    """Deterministic id of the relationship ``condition change → policy change``.

    The payload is the central bank and the two source change ids, joined by
    unit separators so no combination can collide.
    """
    payload = "\x1f".join(
        (central_bank or "", condition_change_id, policy_change_id)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()