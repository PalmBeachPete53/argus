"""Phase 8 — forex fundamentals analysis.

The analyzer is **pure** (``ForexFundamentalsAnalyzer.analyze`` works on
in-memory ``MonetaryPolicyState`` / ``Fact`` objects + a currencies mapping +
a publications mapping + an optional authoritative classifications mapping and
never touches a store) and strictly deterministic.

Fundamental rules (documented in ``docs/FOREX_FUNDAMENTALS.md``):

1. A ``MonetaryPolicyState`` (Phase 7) is a monetary fundamental when its
   ``subject`` is in ``MONETARY_SUBJECTS`` and its value is a real observed
   level. The value is copied verbatim — never invented, never converted.
2. A ``Fact`` (Phase 4) is a macro fundamental when its ``subject`` is in
   ``MACRO_SUBJECTS``, its ``predicate`` is a level (not in
   ``FUNDAMENTAL_EXCLUDED_PREDICATES``) and it carries a value. A fact with no
   such subject is irrelevant and silently skipped.
3. The economy is the currency of the source's central bank, resolved through
   the canonical ``currencies`` mapping (``CentralBank.currency``, never
   hardcoded, never invented). A bank absent from the mapping is skipped with
   an ``unknown_currency`` warning.
4. The observation time is the temporal reference of the source publication
   (``meeting_date`` else ``publication_date``). ``effective_date`` and
   ``period`` are never observation times.
5. The fundamental dimension is the currency-independent lineage ``subject,
   predicate, value_kind, canonical period, qualifier, publication_type``.
6. Each eligible source observation produces exactly one ``ForexFundamental``.

Differential rules:

7. A differential compares two fundamentals of **two different economies** on
   the **same** lineage, anchored on the base observation: the quote is the
   latest observation of that lineage with ``observed_at <= base.observed_at``
   (no look-ahead). Both orientations are generated; each has a distinct
   identity and the convention is never silently inverted.
8. The differential is the **arithmetic difference** ``base_value - quote_value``
   in the same unit/kind (no conversion, no interpretation). A dimension that
   is absent on one side produces no differential (documented absence); a base
   observation with no eligible quote observation produces a ``missing_side``
   warning; a unit mismatch produces an ``incomparable_differential`` warning;
   text / qualitative / date / boolean / range dimensions are observed but by
   nature **not** differentiable (documented property, never a warning).

No economic interpretation is ever attached to a fundamental or differential:
no hawkish/dovish, no stance, no forecast, no fair value, no trading/forex
signal, no ranking, no causality.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from ..facts.base import Fact, FactValue
from ..models import Publication
from ..states.base import MonetaryPolicyState
from .base import (
    FUNDAMENTAL_EXCLUDED_PREDICATES,
    FUNDAMENTAL_SUBJECTS,
    MACRO_SUBJECTS,
    SOURCE_FACT,
    SOURCE_MONETARY_STATE,
    _NUMERIC_KINDS,
    ForexDifferential,
    ForexFundamental,
    ForexFundamentalResult,
)

_UNCOMPARABLE_TYPES = frozenset({"unknown", "other"})


def _dimension_parts(
    subject: str,
    predicate: str,
    value_kind: str | None,
    period,
    qualifier: str,
    pub_type: str,
) -> tuple[str, ...]:
    return (
        subject,
        predicate,
        value_kind or "",
        period.canonical() if period else "",
        qualifier,
        pub_type,
    )


def _lineage_key(parts: tuple[str, ...]) -> str:
    return "\x1f".join(parts)


class ForexFundamentalsAnalyzer:
    """Pure forex fundamentals analyzer."""

    analysis_version = "15.0.0"

    def analyze(
        self,
        *,
        states: list[MonetaryPolicyState] | None = None,
        facts: list[Fact] | None = None,
        currencies: Mapping[str, str] | None = None,
        publications: Mapping[str, Publication] | None = None,
        classifications: Mapping[str, str] | None = None,
    ) -> ForexFundamentalResult:
        """Synthesize the forex fundamentals and differentials of the given
        monetary states and macro facts.

        ``states`` are the ``MonetaryPolicyState`` entries produced by Phase 7 (monetary dimensions); ``facts`` are the ``Fact`` objects produced
        by Phase 4 (macro dimensions). ``currencies`` maps ``central_bank →
        ISO currency`` (the canonical ``CentralBank.currency`` mapping) and is
        the only place an economy is resolved from a bank.

        ``publications`` maps ``publication_id → Publication`` and supplies the
        temporal reference (``meeting_date`` else ``publication_date``) of each
        macro fact's publication. ``classifications`` maps ``publication_id →
        publication_type`` and is the **authoritative** source of the lineage's
        publication-type discriminator (exactly as in Phase 5/6/7). When
        provided, a publication absent from the mapping has no canonical
        classification and its facts are skipped (``missing_classification``
        warning). When ``classifications`` is ``None`` (standalone in-memory
        use), the analyzer falls back to the denormalized
        ``Publication.publication_type`` — documented convenience, never used
        by the production entry point ``analyze_forex_fundamentals``.
        """
        currencies = dict(currencies or {})
        pubs: Mapping[str, Publication] = publications or {}
        result = ForexFundamentalResult()

        fundamentals: list[ForexFundamental] = []
        if states:
            for state in states:
                fundamental = self._monetary(state, currencies, result.warnings)
                if fundamental is not None:
                    fundamentals.append(fundamental)
        if facts:
            for fact in facts:
                fundamental = self._macro(
                    fact, currencies, pubs, classifications, result.warnings
                )
                if fundamental is not None:
                    fundamentals.append(fundamental)

        fundamentals.sort(key=lambda f: f.resolve_id())
        result.fundamentals = fundamentals

        differentials = self._differentials(fundamentals, result.warnings)
        differentials.sort(key=lambda d: d.resolve_id())
        result.differentials = differentials
        return result

    # ------------------------------------------------------------------
    # fundamentals — monetary (Phase 7 states)
    # ------------------------------------------------------------------
    def _monetary(
        self,
        state: MonetaryPolicyState,
        currencies: Mapping[str, str],
        warnings: list[str],
    ) -> ForexFundamental | None:
        currency = currencies.get(state.central_bank or "")
        if not currency:
            warnings.append(f"unknown_currency:{state.central_bank or ''}")
            return None
        if state.subject not in FUNDAMENTAL_SUBJECTS:
            return None
        if state.value is None or state.value.kind is None:
            warnings.append(f"valueless:{state.state_id or state.resolve_id()}")
            return None
        parts = _dimension_parts(
            state.subject,
            state.predicate,
            state.value_kind,
            state.period,
            state.qualifier,
            state.publication_type,
        )
        fundamental = ForexFundamental(
            currency=currency,
            synthesized=True,
            source_kind=SOURCE_MONETARY_STATE,
            source_id=state.resolve_id(),
            central_bank=state.central_bank,
            dimension_key="\x1f".join((currency,) + parts),
            lineage_key=_lineage_key(parts),
            subject=state.subject,
            predicate=state.predicate,
            value_kind=state.value_kind,
            qualifier=state.qualifier,
            period=state.period,
            publication_type=state.publication_type,
            value=state.value,
            observed_at=state.observed_at,
            publication_id=state.publication_id,
            document_id=state.document_id,
            effective_date=state.effective_date,
            source_text=state.source_text,
            analysis_version=self.analysis_version,
        )
        fundamental.resolve_id()
        return fundamental

    # ------------------------------------------------------------------
    # fundamentals — macro (Phase 4 facts)
    # ------------------------------------------------------------------
    def _macro(
        self,
        fact: Fact,
        currencies: Mapping[str, str],
        pubs: Mapping[str, Publication],
        classifications: Mapping[str, str] | None,
        warnings: list[str],
    ) -> ForexFundamental | None:
        if fact.subject not in MACRO_SUBJECTS:
            return None
        if fact.predicate in FUNDAMENTAL_EXCLUDED_PREDICATES:
            warnings.append(f"out_of_scope_fact:{fact.fact_id or fact.resolve_id()}")
            return None
        if fact.value is None or fact.value.kind is None:
            warnings.append(f"valueless:{fact.fact_id or fact.resolve_id()}")
            return None
        if not fact.publication_id:
            warnings.append(f"missing_publication:{fact.fact_id or fact.resolve_id()}")
            return None
        pub = pubs.get(fact.publication_id)
        if pub is None:
            warnings.append(f"missing_publication:{fact.publication_id}")
            return None
        observed_at = pub.meeting_date or pub.publication_date
        if observed_at is None:
            warnings.append(f"undated_publication:{pub.id}")
            return None
        bank = fact.central_bank or pub.central_bank
        if not bank:
            warnings.append(f"unplaced_fact:{fact.fact_id or fact.resolve_id()}")
            return None
        currency = currencies.get(bank)
        if not currency:
            warnings.append(f"unknown_currency:{bank}")
            return None
        pub_type = self._resolve_type(fact, pub, classifications, warnings)
        if pub_type is None:
            return None
        parts = _dimension_parts(
            fact.subject,
            fact.predicate,
            fact.value.kind.value,
            fact.period,
            fact.identity_qualifier or "",
            pub_type,
        )
        fundamental = ForexFundamental(
            currency=currency,
            synthesized=True,
            source_kind=SOURCE_FACT,
            source_id=fact.resolve_id(),
            central_bank=bank,
            dimension_key="\x1f".join((currency,) + parts),
            lineage_key=_lineage_key(parts),
            subject=fact.subject,
            predicate=fact.predicate,
            value_kind=fact.value.kind.value,
            qualifier=fact.identity_qualifier or "",
            period=fact.period,
            publication_type=pub_type,
            value=fact.value,
            observed_at=observed_at,
            publication_id=fact.publication_id,
            document_id=fact.document_id,
            effective_date=fact.effective_date,
            source_text=fact.source_text,
            analysis_version=self.analysis_version,
        )
        fundamental.resolve_id()
        return fundamental

    def _resolve_type(
        self,
        fact: Fact,
        pub: Publication,
        classifications: Mapping[str, str] | None,
        warnings: list[str],
    ) -> str | None:
        if classifications is not None:
            if pub.id not in classifications:
                warnings.append(f"missing_classification:{pub.id}")
                return None
            pub_type = classifications[pub.id]
        else:
            pub_type = pub.publication_type
        if not pub_type or pub_type in _UNCOMPARABLE_TYPES:
            warnings.append(f"unclassified_publication:{pub.id}")
            return None
        return pub_type

    # ------------------------------------------------------------------
    # differentials
    # ------------------------------------------------------------------
    def _differentials(
        self,
        fundamentals: list[ForexFundamental],
        warnings: list[str],
    ) -> list[ForexDifferential]:
        by_currency: dict[str, list[ForexFundamental]] = {}
        for fundamental in fundamentals:
            by_currency.setdefault(fundamental.currency or "", []).append(fundamental)
        currencies = sorted(by_currency)
        differentials: list[ForexDifferential] = []
        for base_currency in currencies:
            for quote_currency in currencies:
                if base_currency == quote_currency:
                    continue
                self._pair_differentials(
                    base_currency,
                    quote_currency,
                    by_currency,
                    differentials,
                    warnings,
                )
        return differentials

    def _pair_differentials(
        self,
        base_currency: str,
        quote_currency: str,
        by_currency: dict[str, list[ForexFundamental]],
        differentials: list[ForexDifferential],
        warnings: list[str],
    ) -> None:
        base_funds = by_currency[base_currency]
        quote_by_lineage: dict[str, list[ForexFundamental]] = {}
        for quote in by_currency[quote_currency]:
            quote_by_lineage.setdefault(quote.lineage_key, []).append(quote)
        for quote_list in quote_by_lineage.values():
            quote_list.sort(key=lambda f: (f.observed_at, f.fundamental_id))
        base_sorted = sorted(base_funds, key=lambda f: (f.observed_at, f.fundamental_id))
        for base in base_sorted:
            quote_list = quote_by_lineage.get(base.lineage_key)
            if not quote_list:
                continue
            quote = None
            for candidate in quote_list:
                if (
                    base.observed_at is not None
                    and candidate.observed_at is not None
                    and candidate.observed_at <= base.observed_at
                ):
                    quote = candidate
                else:
                    break
            if quote is None:
                warnings.append(
                    f"missing_side:{base.lineage_key}:{quote_currency}:"
                    f"{base.observed_at.date().isoformat() if base.observed_at else 'unknown'}"
                )
                continue
            differential = self._build_differential(base, quote, warnings)
            if differential is not None:
                differentials.append(differential)

    def _build_differential(
        self,
        base: ForexFundamental,
        quote: ForexFundamental,
        warnings: list[str],
    ) -> ForexDifferential | None:
        base_value = base.value
        quote_value = quote.value
        if (
            base_value is None
            or quote_value is None
            or base_value.value is None
            or quote_value.value is None
        ):
            return None
        if base_value.kind.value not in _NUMERIC_KINDS:
            return None
        if base_value.unit != quote_value.unit:
            warnings.append(
                f"incomparable_differential:{base.subject}/{base.predicate}:"
                f"{base.currency}:{base_value.unit}:{quote.currency}:{quote_value.unit}"
            )
            return None
        differential = ForexDifferential(
            base_currency=base.currency or "",
            quote_currency=quote.currency or "",
            synthesized=True,
            dimension_key=base.lineage_key,
            subject=base.subject,
            predicate=base.predicate,
            value_kind=base.value_kind,
            qualifier=base.qualifier,
            period=base.period,
            publication_type=base.publication_type,
            base_fundamental_id=base.fundamental_id or base.resolve_id(),
            base_source_kind=base.source_kind,
            base_source_id=base.source_id,
            base_central_bank=base.central_bank,
            base_value=base_value,
            base_observed_at=base.observed_at,
            base_publication_id=base.publication_id,
            base_document_id=base.document_id,
            base_effective_date=base.effective_date,
            base_source_text=base.source_text,
            quote_fundamental_id=quote.fundamental_id or quote.resolve_id(),
            quote_source_kind=quote.source_kind,
            quote_source_id=quote.source_id,
            quote_central_bank=quote.central_bank,
            quote_value=quote_value,
            quote_observed_at=quote.observed_at,
            quote_publication_id=quote.publication_id,
            quote_document_id=quote.document_id,
            quote_effective_date=quote.effective_date,
            quote_source_text=quote.source_text,
            value=FactValue(kind=base_value.kind, value=base_value.value - quote_value.value, unit=base_value.unit),
            analysis_version=self.analysis_version,
        )
        differential.resolve_id()
        differential.formulation = differential.describe()
        return differential


def analyze_forex_fundamentals(
    store,
    *,
    bank: str | None = None,
    persist: bool = True,
) -> ForexFundamentalResult:
    """Recompute the forex fundamentals and differentials of a bank's currency
    (or the whole store) from the current ``monetary_policy_states`` table
    (Phase 7 output) and the ``facts`` table (Phase 4 output), persist them
    idempotently, and return the result (fundamentals + differentials +
    observability warnings).

    Phase 8 consumes Phase 7 states and Phase 4 facts; both must be present.
    Differentials need both sides of every pair, so the analyzer always reads
    the **full** dataset (all states, all facts, all publications,
    classifications) — the ``bank`` scope only limits what is persisted. The
    canonical bank→currency mapping is built from ``SourceRegistry``
    (``CentralBank.currency``) and passed to the pure analyzer.

    The ``forex_fundamentals`` / ``forex_differentials`` tables are derived
    data: ``analyze_forex_fundamentals`` recomputes the full scope and
    *replaces* it (``rebuild_forex_fundamentals`` /
    ``rebuild_forex_differentials``), so repeated runs are idempotent, empty
    results clear the scope, and no derived row can ever survive the
    disappearance of the observation it summarizes. Source ``facts``,
    ``fact_changes``, ``policy_reactions`` and ``monetary_policy_states`` are
    never modified.
    """
    from ..registry import SourceRegistry

    currencies: dict[str, str] = {
        bank_.id: bank_.currency for bank_ in SourceRegistry().banks if bank_.id
    }
    publications = store.list_publications()
    pubs: dict[str, object] = {p.id: p for p in publications if p.id}
    classifications: dict[str, str] = {
        c["publication_id"]: c["publication_type"]
        for c in store.list_classifications()
    }
    states = store.get_policy_states()
    facts = store.get_facts()
    result = ForexFundamentalsAnalyzer().analyze(
        states=states,
        facts=facts,
        currencies=currencies,
        publications=pubs,
        classifications=classifications,
    )
    if persist:
        currency = currencies.get(bank) if bank is not None else None
        if bank is None or currency is not None:
            store.rebuild_forex_fundamentals(result.fundamentals, currency=currency)
            store.rebuild_forex_differentials(
                result.differentials,
                currencies=(currency,) if currency is not None else None,
            )
    return result