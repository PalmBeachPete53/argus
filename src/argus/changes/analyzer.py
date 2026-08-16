"""Phase 5 — temporal / cross-publication change analysis.

The analyzer is **pure** (``FactChangeAnalyzer.analyze`` works on in-memory
Facts + Publications and never touches a store) and strictly deterministic.

Matching rules (documented in ``docs/CHANGES.md``):

1. Two Facts are candidates only when they belong to **different publications**
   and to the *same observation lineage*: same central bank, subject,
   predicate, ``value.kind``, canonical period, identity qualifier **and**
   publication type.
2. The publication type is the **authoritative classification** (from the
   ``classifications`` table, passed as a mapping), never the denormalized
   ``Publication.publication_type`` cache when an authoritative classification
   is available. A publication without any canonical classification is skipped
   (``UNKNOWN > INVENTION``).
3. The comparison direction is decided by the publication temporal reference
   (``meeting_date`` when set, else ``publication_date``); ties are broken by
   publication id. Facts are then chained *consecutively* (F1→F2, F2→F3), never
   against a fixed baseline. Each adjacent pair is evaluated independently: a
   pair that yields no change (identical values) or cannot be compared (e.g.
   incompatible units) produces no change for that pair and is never jumped
   over to bridge to a later observation; the pair immediately following an
   incomparable pair is still evaluated. Observations of different lineages
   never interact, so the consecutive pair of a lineage is the next observation
   of that lineage.
4. Identical values produce **no change**. A numeric difference produces
   ``numeric_changed`` with ``delta = current − previous`` (same kind/unit,
   rounded to 10 decimals); a categorical difference produces
   ``qualitative_changed``; a verbatim wording difference produces
   ``text_changed``. Period mismatch (e.g. a 2027 vs a 2028 forecast) and
   different identity qualifiers never match, so they never change.
5. Facts whose publication is missing, whose classification is missing/unknown,
   undated, valueless, or which carry no document id are skipped with an
   observability warning (precision over recall: better no change than a
   spurious one).

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
    pub_type: str


class FactChangeAnalyzer:
    """Pure temporal/cross-publication analyzer."""

    analysis_version = "12.1.0"

    def analyze(
        self,
        facts: list[Fact],
        *,
        publications: Mapping[str, Publication] | None = None,
        classifications: Mapping[str, str] | None = None,
    ) -> FactChangeResult:
        """Derive the changes between consecutive comparable observations.

        ``facts`` are the Facts to relate; ``publications`` maps
        ``publication_id → Publication`` and supplies the temporal reference
        used to order observations.

        ``classifications`` maps ``publication_id → publication_type`` and is
        the **authoritative** source of the publication type. When provided,
        ``Publication.publication_type`` is never trusted: a publication absent
        from the mapping has **no canonical classification** and is skipped
        (``missing_classification`` warning). When ``classifications`` is
        ``None`` (standalone in-memory use), the analyzer falls back to the
        denormalized ``Publication.publication_type`` — documented convenience,
        never used by the production entry point ``analyze_changes``.
        """
        pubs: Mapping[str, Publication] = publications or {}
        result = FactChangeResult()

        # (bank, subject, predicate, value_kind, period, qualifier, pub_type)
        groups: dict[tuple, list[_Entry]] = {}
        for fact in facts:
            entry = self._prepare(fact, pubs, classifications, result.warnings)
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
        classifications: Mapping[str, str] | None,
        warnings: list[str],
    ) -> _Entry | None:
        if not fact.publication_id:
            warnings.append(f"missing_publication:{fact.publication_id}")
            return None
        pub = pubs.get(fact.publication_id)
        if pub is None:
            warnings.append(f"missing_publication:{fact.publication_id}")
            return None
        if not fact.document_id:
            warnings.append(f"undocumented_fact:{fact.fact_id or fact.compute_fact_id()}")
            return None
        pub_type = self._resolve_type(pub, classifications, warnings)
        if pub_type is None:
            return None
        reference = pub.meeting_date or pub.publication_date
        if reference is None:
            warnings.append(f"undated_publication:{pub.id}")
            return None
        if fact.value is None or fact.value.kind is None or fact.value.kind is ValueKind.NULL:
            warnings.append(f"valueless_fact:{fact.fact_id or fact.compute_fact_id()}")
            return None
        return _Entry(fact=fact, publication=pub, reference=reference, pub_type=pub_type)

    def _resolve_type(
        self,
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

    def _key(self, entry: _Entry) -> tuple:
        fact, pub = entry.fact, entry.publication
        return (
            fact.central_bank or pub.central_bank,
            fact.subject,
            fact.predicate,
            fact.value.kind,
            fact.period.canonical() if fact.period else "",
            fact.identity_qualifier or "",
            entry.pub_type,
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
            return self._build(prev_fact, cur_fact, previous.publication, ChangeType.NUMERIC, delta)

        if before.kind is ValueKind.TEXT:
            if (before.value or "") == (after.value or ""):
                return None
            return self._build(
                prev_fact, cur_fact, previous.publication, ChangeType.TEXT, None
            )

        # categorical / date / boolean / range / null — exact comparison only.
        if self._qualitative_equal(before, after):
            return None
        return self._build(
            prev_fact, cur_fact, previous.publication, ChangeType.QUALITATIVE, None
        )

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
        prev_pub: Publication,
        change_type: ChangeType,
        delta: FactValue | None,
    ) -> FactChange:
        return FactChange(
            previous_fact_id=prev_fact.fact_id,
            current_fact_id=cur_fact.fact_id,
            change_type=change_type,
            # Fact.central_bank wins; the publication's bank is the documented
            # fallback, and a fully unplaced change keeps None (never invented).
            central_bank=prev_fact.central_bank or prev_pub.central_bank,
            subject=prev_fact.subject,
            predicate=prev_fact.predicate,
            value_kind=prev_fact.value.kind.value if prev_fact.value else None,
            previous_value=prev_fact.value,
            current_value=cur_fact.value,
            delta=delta,
            identity_qualifier=prev_fact.identity_qualifier or "",
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
) -> FactChangeResult:
    """Recompute the changes of a bank (or the whole store) from the current
    facts table, persist them idempotently, and return the result (changes +
    observability warnings).

    The publication type is taken from the authoritative ``classifications``
    table (never from the denormalized ``publications.publication_type``
    cache), matching the architecture: ``classifications → publication type →
    FactChange matching``.

    The ``fact_changes`` table is derived data: ``analyze_changes`` recomputes
    the full bank scope and *replaces* it (``rebuild_changes``), so repeated
    runs are idempotent, empty results clear the scope, and a change can never
    survive the disappearance of the facts it relates.
    """
    publications = store.list_publications(bank=bank)
    pubs: dict[str, Publication] = {p.id: p for p in publications if p.id}
    classifications: dict[str, str] = {
        c["publication_id"]: c["publication_type"]
        for c in store.list_classifications(bank=bank)
    }
    facts = store.get_facts(bank=bank)
    result = FactChangeAnalyzer().analyze(
        facts, publications=pubs, classifications=classifications
    )
    if persist:
        store.rebuild_changes(result.changes, bank=bank)
    return result