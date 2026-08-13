"""Phase 5 — type-specific decision extractors.

An extractor converts a ``NormalizedDocument`` of a monetary policy decision
into a list of provenance-carrying ``Fact`` assertions, following the
``ExtractionResult`` contract defined in Phase 4 (``facts/base.py``).

Architecture:
    NormalizedDocument
        ↓ DecisionExtractor.extract(publication, document)
    ExtractionResult(publication_id, document_id, facts, warnings)
        ↓ Store.rebuild_facts_for_document / save_facts
    facts table

Extractors are per-bank (bank-specific wording is encapsulated, invariant 10);
the generic engine in this module only dispatches on ``central_bank``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..facts import ExtractionResult

if TYPE_CHECKING:
    from ..models import Publication
    from ..documents.base import NormalizedDocument

# Canonical type a decision extractor is allowed to process. Extraction below
# this type is skipped by the store integration helper, so a press conference or
# a minutes document is never mistakenly mined for decision facts.
DECISION_PUBLICATION_TYPE = "monetary_policy_decision"


class DecisionExtractor(ABC):
    """Extracts structured Facts from one decision ``NormalizedDocument``.

    Concrete extractors are pure: ``extract`` reads the document and returns an
    ``ExtractionResult``. Persistence is the caller's concern
    (``extract_decision`` / ``Store``), keeping extraction auditable and
    reproducible.
    """

    bank: str = ""
    extraction_version: str = ""

    def __init__(self) -> None:
        if not self.bank or not self.extraction_version:
            raise TypeError(f"{self.__class__.__name__} must define bank and extraction_version")

    @abstractmethod
    def extract(self, publication, document) -> ExtractionResult:  # pragma: no cover
        """Return Facts for ``document`` (exactly one per (publication, document))."""
        raise NotImplementedError


def extract_decision(
    store,
    publication: Publication,
    *,
    document=None,
    extractor=None,
    expected_type: str = DECISION_PUBLICATION_TYPE,
) -> list[ExtractionResult]:
    """Run the decision extractor for ``publication`` and persist its Facts.

    ``store`` is the existing SQLite ``Store``. The normalized document(s) come
    from the store unless a specific ``document`` is given. Facts are persisted
    with ``Store.rebuild_facts_for_document`` (delete + insert), so re-running
    never leaves stale rows.

    Offline by construction: nothing here touches the network.

    Returns the list of ``ExtractionResult`` (empty if no extractor applies or
    the publication is not classified as a decision).
    """
    extractor = extractor or get_extractor(publication.central_bank)
    if extractor is None:
        return []
    if not _is_decision_publication(store, publication, expected_type=expected_type):
        return []
    documents = [document] if document is not None else store.normalized_documents_for_publication(publication.id or "")
    results: list[ExtractionResult] = []
    for doc in documents:
        if not getattr(doc, "ok", False):
            continue
        result = extractor.extract(publication, doc)
        if result.facts:
            store.rebuild_facts_for_document(doc.document_id, result)
        results.append(result)
    return results


def extract_decision_batch(store, *, bank: str | None = None) -> list[ExtractionResult]:
    """Run ``extract_decision`` over all stored publications (optionally one
    ``bank``). Returns the aggregated results."""
    publications = store.list_publications(bank=bank)
    results: list[ExtractionResult] = []
    for publication in publications:
        if not publication.id:
            continue
        results.extend(extract_decision(store, publication))
    return results


def _is_decision_publication(store, publication, *, expected_type: str) -> bool:
    """Gate extraction to decision publications.

    The authoritative record lives in ``classifications``; the denormalized
    ``publication.publication_type`` cache is only a fallback. Publications with
    *no* classification yet are allowed (classification may run afterwards).
    """
    record = store.get_classification(publication.id or "") if publication.id else None
    if record is not None:
        return record["publication_type"] == expected_type
    cached = publication.publication_type
    if cached and cached != expected_type:
        return False
    return True


from .ecb import EcbDecisionExtractor  # noqa: E402  (see below)

_EXTRACTORS: dict[str, DecisionExtractor] = {
    EcbDecisionExtractor.bank: EcbDecisionExtractor(),
}


def get_extractor(bank: str) -> DecisionExtractor | None:
    return _EXTRACTORS.get(bank)