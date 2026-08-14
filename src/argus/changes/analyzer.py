"""Phase 12 — temporal / cross-publication change analysis.

The analyzer is **pure** (``FactChangeAnalyzer.analyze`` works on in-memory
Facts + Publications and never touches a store) and strictly deterministic.

Matching rules (documented in ``docs/CHANGES.md``):

1. Two Facts are candidates only when they belong to **different publications**
   and to the *same observation lineage*: same central bank, subject,
   predicate, ``value.kind``, canonical period, identity qualifier **and**
   publication type.
2. The comparison direction is decided by the publication temporal reference
   (``meeting_date`` when set, else ``publication_date``); ties are broken by
   publication id. Facts are then chained *consecutively* (F1→F2, F2→F3), never
   against a fixed baseline.
3. Identical values produce **no change**. A numeric difference produces
   ``numeric_changed`` with ``delta = current − previous`` (same kind/unit,
   rounded to 10 decimals); a categorical difference produces
   ``qualitative_changed``; a verbatim wording difference produces
   ``text_changed``. Period mismatch (e.g. a 2027 vs a 2028 forecast) and
   different identity qualifiers never match, so they never change.
4. Facts whose publication is missing, unclassified (``unknown``/``other``),
   undated, or which carry no value are skipped with an observability warning
   (precision over recall: better no change than a spurious one).

No economic interpretation is ever attached to a change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from ..facts.base import Fact, FactPeriod, FactValue, ValueKind
from ..models import Publication
from ..normalize import iso
from .base import ChangeType, FactChange, FactChangeResult

NUMERIC_KINDS = frozenset(
    {ValueKind.NUMBER, ValueKind.PERCENTAGE, ValueKind.BASIS_POINTS, ValueKind.CURRENCY}
)

# Publication types that never describe a comparable observation lineage.
_UNCOMPARABLE_TYPES = frozenset({"unknown", "other"})


@dataclass
class _Entry:
    """A Fact joined with the publication-level context it needs to be
    ordered and compared."""

    fact: Fact
    publication: Publication
    reference: datetime


class FactChangeAnalyzer:
    """Pure temporal/cross-publication analyzer."""

    analysis_version = "12.0.0"

    def analyze(
        self,
        facts: list[Fact],
        *,
        publications: Mapping[str, Publication] | None = None,
    ) -> FactChangeResult:
        """Derive the changes between consecutive comparable observations.

        ``facts`` are the Facts to relate; ``publications`` maps
        ``publication_id → Publication`` and supplies the publication type and
        the temporal reference used to order observations. Facts whose
        publication is absent from the mapping are skipped (warning).
        """
        pubs: Mapping[str, Publication] = publications or {}
        result = FactChangeResult()

        # (bank, subject, predicate, value_kind, period, qualifier, pub_type)
        groups: dict[tuple, list[_Entry]] = {}
        for fact in facts:
            entry = self._prepare(fact, pubs, result.warnings)
            if entry is None:
                continue
            key = self._key(entry)
            groups.setdefault(key, []).append(entry)

        changes: list[FactChange] = []
        for entries in groups.values():
            entries.sort(key=lambda e: (iso(e.reference), e.publication.id or ""))
            for previous, current in zip(entries, entries[1:]):
                if previous.publication.id == current.publication.id:
                    continue
                change = self._compare(previous, current)
                if change is not None:
                    changes.append(change)

        changes.sort(key=lambda c: c.resolve_id())
        result.changes = changes
        return result

    # ------------------------------------------------------------------
    # preparation
    # ------------------------------------------------------------------
    def _prepare(
        self,
        fact: Fact,
        pubs: Mapping[str, Publication],
        warnings: list[str],
    ) -> _Entry | None:
        pub = pubs.get(fact.publication_id)
        if pub is None:
            warnings.append(f"missing_publication:{fact.publication_id}")
            return None
        pub_type = pub.publication_type
        if not pub_type or pub_type in _UNCOMPARABLE_TYPES:
            warnings.append(f"unclassified_publication:{pub.id}")
            return None
        reference = pub.meeting_date or pub.publication_date
        if reference is None:
            warnings.append(f"undated_publication:{pub.id}")
            return None
        if fact.value is None or fact.value.kind is None or fact.value.kind is ValueKind.NULL:
            warnings.append(f"valueless_fact:{fact.fact_id or fact.compute_fact_id()}")
            return None
        return _Entry(fact=fact, publication=pub, reference=reference)

    def _key(self, entry: _Entry) -> tuple:
        fact, pub = entry.fact, entry.publication
        return (
            fact.central_bank or pub.central_bank,
            fact.subject,
            fact.predicate,
            fact.value.kind,
            fact.period.canonical() if fact.period else "",
            fact.identity_qualifier,
            pub.publication_type,
        )

    # ------------------------------------------------------------------
    # comparison
    # ------------------------------------------------------------------
    def _compare(self, previous: _Entry, current: _Entry) -> FactChange | None:
        prev_fact, cur_fact = previous.fact, current.fact
        before, after = prev_fact.value, cur_fact.value

        if before.kind in NUMERIC_KINDS:
            if before.value is None or after.value is None:
                return None
            if (before.unit or "") != (after.unit or ""):
                return None
            if before.value == after.value:
                return None
            delta = FactValue(
                before.kind,
                value=round(float(after.value) - float(before.value), 10),
                unit=before.unit,
            )
            return self._build(prev_fact, cur_fact, ChangeType.NUMERIC, delta)

        if before.kind is ValueKind.TEXT:
            if (before.value or "") == (after.value or ""):
                return None
            return self._build(prev_fact, cur_fact, ChangeType.TEXT, None)

        # categorical / date / boolean / range / null — exact comparison only.
        if self._qualitative_equal(before, after):
            return None
        return self._build(prev_fact, cur_fact, ChangeType.QUALITATIVE, None)

    @staticmethod
    def _qualitative_equal(before: FactValue, after: FactValue) -> bool:
        if before.kind is ValueKind.RANGE:
            return before.min == after.min and before.max == after.max
        if before.kind is ValueKind.NULL:
            return True
        return before.value == after.value

    def _build(
        self,
        prev_fact: Fact,
        cur_fact: Fact,
        change_type: ChangeType,
        delta: FactValue | None,
    ) -> FactChange:
        return FactChange(
            previous_fact_id=prev_fact.fact_id,
            current_fact_id=cur_fact.fact_id,
            change_type=change_type,
            central_bank=prev_fact.central_bank,
            subject=prev_fact.subject,
            predicate=prev_fact.predicate,
            value_kind=prev_fact.value.kind.value if prev_fact.value else None,
            previous_value=prev_fact.value,
            current_value=cur_fact.value,
            delta=delta,
            identity_qualifier=prev_fact.identity_qualifier,
            previous_period=prev_fact.period,
            current_period=cur_fact.period,
            previous_publication_id=prev_fact.publication_id,
            current_publication_id=cur_fact.publication_id,
            previous_document_id=prev_fact.document_id,
            current_document_id=cur_fact.document_id,
            previous_effective_date=prev_fact.effective_date,
            current_effective_date=cur_fact.effective_date,
            previous_source_text=prev_fact.source_text,
            current_source_text=cur_fact.source_text,
            analysis_version=self.analysis_version,
        )


def analyze_changes(
    store,
    *,
    bank: str | None = None,
    persist: bool = True,
) -> list[FactChange]:
    """Recompute the changes of a bank (or the whole store) from the current
    facts table, persist them idempotently, and return them.

    The ``fact_changes`` table is derived data: ``analyze_changes`` recomputes
    the full bank scope and *replaces* it (``rebuild_changes``), so repeated
    runs are idempotent, empty results clear the scope, and a change can never
    survive the disappearance of the facts it relates.
    """
    publications = store.list_publications(bank=bank)
    pubs: dict[str, Publication] = {p.id: p for p in publications if p.id}
    facts = store.get_facts(bank=bank)
    result = FactChangeAnalyzer().analyze(facts, publications=pubs)
    if persist:
        store.rebuild_changes(result.changes, bank=bank)
    return result.changes