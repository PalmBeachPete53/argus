"""Phase 6 — type-specific monetary policy statement extractors.

An extractor converts a ``NormalizedDocument`` of a monetary policy statement
into a list of provenance-carrying ``Fact`` assertions, following the
``ExtractionResult`` contract defined in Phase 4 (``facts/base.py``).

Architecture:
    NormalizedDocument
        ↓ StatementExtractor.extract(publication, document)
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

# Canonical type a statement extractor is allowed to process. Extraction below
# this type is skipped by the store integration helper, so a decision or a
# minutes document is never mistakenly mined for statement facts.
STATEMENT_PUBLICATION_TYPE = "monetary_policy_statement"


class StatementExtractor(ABC):
    """Extracts structured Facts from one statement ``NormalizedDocument``.

    Concrete extractors are pure: ``extract`` reads the document and returns an
    ``ExtractionResult``. Persistence is the caller's concern
    (``extract_statement`` / ``Store``), keeping extraction auditable and
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


def extract_statement(
    store,
    publication: Publication,
    *,
    document=None,
    extractor=None,
    expected_type: str = STATEMENT_PUBLICATION_TYPE,
) -> list[ExtractionResult]:
    """Run the statement extractor for ``publication`` and persist its Facts.

    ``store`` is the existing SQLite ``Store``. The normalized document(s) come
    from the store unless a specific ``document`` is given. Facts are persisted
    with ``Store.rebuild_facts_for_document`` (delete + insert), so re-running
    never leaves stale rows.

    Offline by construction: nothing here touches the network.

    The current extraction result is the source of truth for the persisted
    state: ``rebuild_facts_for_document`` (delete + insert) runs for every
    valid normalized document, **including when the result is empty**, so a
    re-extraction that now yields no facts clears the stale facts of that
    document instead of leaving them behind.

    Returns the list of ``ExtractionResult`` (empty if no extractor applies or
    the publication is not classified as ``monetary_policy_statement``).
    """
    extractor = extractor or get_extractor(publication.central_bank)
    if extractor is None:
        return []
    if not _is_statement_publication(store, publication, expected_type=expected_type):
        return []
    documents = [document] if document is not None else store.normalized_documents_for_publication(publication.id or "")
    results: list[ExtractionResult] = []
    for doc in documents:
        if not getattr(doc, "ok", False):
            continue
        result = extractor.extract(publication, doc)
        store.rebuild_facts_for_document(doc.document_id, result)
        results.append(result)
    return results


def extract_statement_batch(store, *, bank: str | None = None) -> list[ExtractionResult]:
    """Run ``extract_statement`` over all stored publications (optionally one
    ``bank``). Returns the aggregated results."""
    publications = store.list_publications(bank=bank)
    results: list[ExtractionResult] = []
    for publication in publications:
        if not publication.id:
            continue
        results.extend(extract_statement(store, publication))
    return results


def _is_statement_publication(store, publication, *, expected_type: str) -> bool:
    """Gate extraction to statement publications.

    The ``classifications`` table is the **single source of truth**: extraction
    is authorized only when an authoritative classification record exists for
    the publication and its ``publication_type`` is ``monetary_policy_statement``.
    The denormalized ``publication.publication_type`` cache is never used to
    infer authorization, and an absent classification refuses extraction —
    classification must run first (classification → extraction).
    """
    if not publication.id:
        return False
    record = store.get_classification(publication.id)
    if record is None:
        return False
    return record["publication_type"] == expected_type


from .ecb import EcbMonetaryPolicyStatementExtractor  # noqa: E402  (see below)

_EXTRACTORS: dict[str, StatementExtractor] = {
    EcbMonetaryPolicyStatementExtractor.bank: EcbMonetaryPolicyStatementExtractor(),
}


def get_extractor(bank: str) -> StatementExtractor | None:
    return _EXTRACTORS.get(bank)


from .boj import BojStatementExtractor  # noqa: E402
from .boc import BocStatementExtractor  # noqa: E402
from .boe import BoeStatementExtractor  # noqa: E402
from .fed import FedStatementExtractor  # noqa: E402
from .rba import RbaStatementExtractor  # noqa: E402
from .rbnz import RbnzStatementExtractor  # noqa: E402
from .riksbank import RiksbankStatementExtractor  # noqa: E402
from .snb import SnbStatementExtractor  # noqa: E402

for _extractor in (
    BojStatementExtractor,
    BocStatementExtractor,
    BoeStatementExtractor,
    FedStatementExtractor,
    RbaStatementExtractor,
    RbnzStatementExtractor,
    RiksbankStatementExtractor,
    SnbStatementExtractor,
):
    if _extractor.bank not in _EXTRACTORS:
        _EXTRACTORS[_extractor.bank] = _extractor()