"""Phase 5 — deterministic identity of a ``FactChange``.

A ``change_id`` is derived solely from the two source Facts and the change
kind, so the *same pair of facts observed the same way always yields the same
id* — the id is reproducible, explains itself (previous → current), and is
stable across rebuilds (idempotent persistence).
"""

from __future__ import annotations

import hashlib

from .base import ChangeType


def change_id_of(
    *,
    previous_fact_id: str,
    current_fact_id: str,
    change_type: ChangeType | str,
) -> str:
    """Deterministic id of the relation ``previous → current``.

    The payload is the previous fact id, the current fact id and the change
    kind, joined by unit separators so no combination can collide.
    """
    ctype = change_type.value if isinstance(change_type, ChangeType) else str(change_type)
    payload = "\x1f".join((previous_fact_id, current_fact_id, ctype))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()