"""Phase 4.5 — type-specific economic projections extractors.

An extractor converts a ``NormalizedDocument`` of a macroeconomic projections
publication (the ECB/Eurosystem "staff macroeconomic projections for the euro
area") into a list of provenance-carrying ``Fact`` assertions, following the
``ExtractionResult`` contract defined in Phase 4 (``facts/base.py``).

Architecture:
    NormalizedDocument
        ↓ ProjectionsExtractor.extract(publication, document)
    ExtractionResult(publication_id, document_id, facts, warnings)
        ↓ Store.rebuild_facts_for_document
    facts table

Extractors are per-bank (bank-specific wording and table layout are
encapsulated, invariant 10); the generic engine in this module only dispatches
on ``central_bank``.

Economic projections are **table documents**: the projection values live in
structured tables whose columns are years and whose rows are variables. The
Phase 4.5 extractor therefore works on ``NormalizedDocument.tables``
(``DocumentTable``: ``headers`` / ``rows``), preserving the
variable × year × value × unit integrity of the source table — it never
re-parses a flattened text blob. A cell is only ever turned into a Fact when it
can be identified by a recognised variable (row label), a year (column header)
and an explicit unit; a bare number without that identity is never a Fact
(``UNKNOWN ≠ PROJECTION``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..facts import ExtractionResult

if TYPE_CHECKING:
    from ..models import Publication
    from ..documents.base import NormalizedDocument

# Canonical type an economic projections extractor is allowed to process.
# Extraction outside this type is skipped by the store integration helper, so a
# decision, a statement or a minutes document is never mistakenly mined for
# projection facts.
PROJECTIONS_PUBLICATION_TYPES = ("economic_projections",)


class ProjectionsExtractor(ABC):
    """Extracts structured Facts from one projections ``NormalizedDocument``.

    Concrete extractors are pure: ``extract`` reads the document and returns an
    ``ExtractionResult``. Persistence is the caller's concern
    (``extract_projections`` / ``Store``), keeping extraction auditable and
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


def extract_projections(
    store,
    publication: Publication,
    *,
    document=None,
    extractor=None,
    expected_types: tuple[str, ...] = PROJECTIONS_PUBLICATION_TYPES,
) -> list[ExtractionResult]:
    """Run the projections extractor for ``publication`` and persist its Facts.

    ``store`` is the existing SQLite ``Store``. The normalized document(s) come
    from the store unless a specific ``document`` is given. Facts are persisted
    with ``Store.rebuild_facts_for_document`` (delete + insert), so re-running
    never leaves stale rows.

    Offline by construction: nothing here touches the network.

    The current extraction result is the source of truth for the persisted
    state: ``rebuild_facts_for_document`` (delete + insert) runs for every
    valid normalized document, **including when the result is empty**, so a
    re-extraction that now yields no facts clears the stale facts of that
    document instead of leaving them behind (same guarantee as Phases 4.1–4.4).

    Returns the list of ``ExtractionResult`` (empty if no extractor applies or
    the publication is not classified as ``economic_projections``).
    """
    extractor = extractor or get_extractor(publication.central_bank)
    if extractor is None:
        return []
    if not _is_projections_publication(store, publication, expected_types=expected_types):
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


def extract_projections_batch(store, *, bank: str | None = None) -> list[ExtractionResult]:
    """Run ``extract_projections`` over all stored publications (optionally one
    ``bank``). Returns the aggregated results."""
    publications = store.list_publications(bank=bank)
    results: list[ExtractionResult] = []
    for publication in publications:
        if not publication.id:
            continue
        results.extend(extract_projections(store, publication))
    return results


def _is_projections_publication(store, publication, *, expected_types: tuple[str, ...]) -> bool:
    """Gate extraction to economic projections publications.

    The ``classifications`` table is the **single source of truth** (same strict
    mechanism as Phases 4.1–4.4): extraction is authorized only when an
    authoritative classification record exists for the publication and its
    ``publication_type`` is ``economic_projections``. The denormalized
    ``publication.publication_type`` cache is never used to infer
    authorization, and an absent classification refuses extraction —
    classification must run first (classification → extraction).
    """
    if not publication.id:
        return False
    record = store.get_classification(publication.id)
    if record is None:
        return False
    return record["publication_type"] in expected_types


from .ecb import EcbProjectionsExtractor  # noqa: E402  (see below)

_EXTRACTORS: dict[str, ProjectionsExtractor] = {
    EcbProjectionsExtractor.bank: EcbProjectionsExtractor(),
}


def get_extractor(bank: str) -> ProjectionsExtractor | None:
    return _EXTRACTORS.get(bank)


from .fed import FedSepExtractor  # noqa: E402

if FedSepExtractor.bank not in _EXTRACTORS:
    _EXTRACTORS[FedSepExtractor.bank] = FedSepExtractor()

from .boj import BojProjectionsExtractor  # noqa: E402

if BojProjectionsExtractor.bank not in _EXTRACTORS:
    _EXTRACTORS[BojProjectionsExtractor.bank] = BojProjectionsExtractor()