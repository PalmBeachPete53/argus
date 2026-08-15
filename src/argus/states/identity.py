"""Phase 14 — deterministic identity of a ``MonetaryPolicyState``.

A ``state_id`` is derived solely from the relationship itself — the central
bank and the source change id — so the *same change observed the same way
always yields the same id*. It is reproducible, self-explanatory (which change
established this observation) and stable across rebuilds (idempotent
persistence), and never "invented": the source is a real ``FactChange``.
"""

from __future__ import annotations

import hashlib


def state_id_of(
    *,
    central_bank: str | None,
    source_change_id: str,
) -> str:
    """Deterministic id of the state observation established by one change.

    The payload is the central bank and the source change id, joined by a unit
    separator so no combination can collide.
    """
    payload = "\x1f".join((central_bank or "", source_change_id))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()