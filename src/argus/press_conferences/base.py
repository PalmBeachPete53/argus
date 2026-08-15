"""Phase 7 — type-specific press conference extractors.

An extractor converts a ``NormalizedDocument`` of a press conference transcript
into a list of provenance-carrying ``Fact`` assertions, following the
``ExtractionResult`` contract defined in Phase 4 (``facts/base.py``).

Architecture:
    NormalizedDocument
        ↓ PressConferenceExtractor.extract(publication, document)
    ExtractionResult(publication_id, document_id, facts, warnings)
        ↓ Store.rebuild_facts_for_document
    facts table

Extractors are per-bank (bank-specific wording and transcript labels are
encapsulated, invariant 10); the generic engine in this module only dispatches
on ``central_bank``.

A press conference transcript differs from a decision/statement: it mixes the
collective **introductory statement** (remarks) with the **individual answers**
of officials to journalists' questions. The Phase 7 extractors therefore keep,
per Fact, the attribution context (remarks vs Q&A answer, the Q&A turn, and the
verbatim official speaker when the document labels one) in ``identity_qualifier``
and the ``Fact.speaker`` attribute — an individual's words are never presented
as a collective decision (roadmap Phase 7 criterion).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..facts import ExtractionResult

if TYPE_CHECKING:
    from ..models import Publication
    from ..documents.base import NormalizedDocument

# Canonical type a press conference extractor is allowed to process. Extraction
# below this type is skipped by the store integration helper, so a decision or a
# minutes document is never mistakenly mined for press conference facts.
PRESS_CONFERENCE_PUBLICATION_TYPE = "press_conference"


class PressConferenceExtractor(ABC):
    """Extracts structured Facts from one press conference ``NormalizedDocument``.

    Concrete extractors are pure: ``extract`` reads the document and returns an
    ``ExtractionResult``. Persistence is the caller's concern
    (``extract_press_conference`` / ``Store``), keeping extraction auditable and
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


def extract_press_conference(
    store,
    publication: Publication,
    *,
    document=None,
    extractor=None,
    expected_type: str = PRESS_CONFERENCE_PUBLICATION_TYPE,
) -> list[ExtractionResult]:
    """Run the press conference extractor for ``publication`` and persist its Facts.

    ``store`` is the existing SQLite ``Store``. The normalized document(s) come
    from the store unless a specific ``document`` is given. Facts are persisted
    with ``Store.rebuild_facts_for_document`` (delete + insert), so re-running
    never leaves stale rows.

    Offline by construction: nothing here touches the network.

    The current extraction result is the source of truth for the persisted
    state: ``rebuild_facts_for_document`` (delete + insert) runs for every
    valid normalized document, **including when the result is empty**, so a
    re-extraction that now yields no facts clears the stale facts of that
    document instead of leaving them behind (same guarantee as Phase 6).

    Returns the list of ``ExtractionResult`` (empty if no extractor applies or
    the publication is not classified as ``press_conference``).
    """
    extractor = extractor or get_extractor(publication.central_bank)
    if extractor is None:
        return []
    if not _is_press_conference_publication(store, publication, expected_type=expected_type):
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


def extract_press_conference_batch(store, *, bank: str | None = None) -> list[ExtractionResult]:
    """Run ``extract_press_conference`` over all stored publications (optionally
    one ``bank``). Returns the aggregated results."""
    publications = store.list_publications(bank=bank)
    results: list[ExtractionResult] = []
    for publication in publications:
        if not publication.id:
            continue
        results.extend(extract_press_conference(store, publication))
    return results


def _is_press_conference_publication(store, publication, *, expected_type: str) -> bool:
    """Gate extraction to press conference publications.

    The ``classifications`` table is the **single source of truth** (same strict
    mechanism as Phase 6, ``statements/base.py``): extraction is authorized only
    when an authoritative classification record exists for the publication and
    its ``publication_type`` is ``press_conference``. The denormalized
    ``publication.publication_type`` cache is never used to infer authorization,
    and an absent classification refuses extraction — classification must run
    first (classification → extraction).
    """
    if not publication.id:
        return False
    record = store.get_classification(publication.id)
    if record is None:
        return False
    return record["publication_type"] == expected_type


from .ecb import EcbPressConferenceExtractor  # noqa: E402  (see below)
from .fed import FedPressConferenceExtractor  # noqa: E402  (see below)
from .boe import BoEPressConferenceExtractor  # noqa: E402  (see below)

_EXTRACTORS: dict[str, PressConferenceExtractor] = {
    EcbPressConferenceExtractor.bank: EcbPressConferenceExtractor(),
    FedPressConferenceExtractor.bank: FedPressConferenceExtractor(),
    BoEPressConferenceExtractor.bank: BoEPressConferenceExtractor(),
}


def get_extractor(bank: str) -> PressConferenceExtractor | None:
    return _EXTRACTORS.get(bank)
