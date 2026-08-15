"""Phase 15 — deterministic identity of a ``ForexFundamental`` and a
``ForexDifferential``.

A ``fundamental_id`` is derived solely from the relationship itself — the
economy (currency), the source kind and the source observation id — so the
*same source observation observed the same way always yields the same id*. It
is reproducible, self-explanatory (which observation established this
fundamental) and stable across rebuilds (idempotent persistence), and never
"invented": the source is a real ``MonetaryPolicyState`` entry or ``Fact``.

A ``differential_id`` is derived from the ordered pair, the dimension
(``subject`` / ``predicate``) and the two source observation ids, so
``EUR/USD`` never collides with ``USD/EUR`` (the orientation is part of the
identity) and two different source observations never collide.
"""

from __future__ import annotations

import hashlib


def fundamental_id_of(
    *,
    currency: str | None,
    source_kind: str,
    source_id: str,
) -> str:
    """Deterministic id of the fundamental established by one source
    observation of one economy.

    The payload is the currency, the source kind and the source observation
    id, joined by a unit separator so no combination can collide.
    """
    payload = "\x1f".join((currency or "", source_kind, source_id))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def differential_id_of(
    *,
    base_currency: str,
    quote_currency: str,
    subject: str,
    predicate: str,
    base_source_id: str,
    quote_source_id: str,
) -> str:
    """Deterministic id of the differential between two source observations of
    two economies.

    The payload is the ordered pair, the dimension and the two source
    observation ids, joined by a unit separator so no combination can collide.
    """
    payload = "\x1f".join(
        (
            base_currency,
            quote_currency,
            subject,
            predicate,
            base_source_id,
            quote_source_id,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()