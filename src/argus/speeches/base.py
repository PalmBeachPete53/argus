"""Phase 11 — type-specific speech extractors.

An extractor converts a ``NormalizedDocument`` of a speech / remarks / address
(the ECB "Speech" publication) into a list of provenance-carrying ``Fact``
assertions, following the ``ExtractionResult`` contract defined in Phase 4
(``facts/base.py``).

Architecture:
    NormalizedDocument
        ↓ SpeechExtractor.extract(publication, document)
    ExtractionResult(publication_id, document_id, facts, warnings)
        ↓ Store.rebuild_facts_for_document
    facts table

Extractors are per-bank (bank-specific wording and section labels are
encapsulated, invariant 10); the generic engine in this module only dispatches
on ``central_bank``.

A speech is the **individual** communication of one central bank official.
Phase 11 therefore preserves, in provenance, the individual nature of the
content: the explicit speaker label is kept on every Fact (``Fact.speaker``,
verbatim, never inferred) and a speech is never mistaken for a collective
decision (no Phase 5–10 subjects, gated on ``speech`` publications). Its
cardinal rule, like Phase 10, is **precision over recall**: a Fact is only
produced from an explicit economic assertion with sufficient identity (subject
+ predicate + value + unit + period when applicable) + provenance, and the
speaker's own words are never confused with personal anecdote, biography,
ceremonial thanks, historical narrative or quoted authors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..facts import ExtractionResult

if TYPE_CHECKING:
    from ..models import Publication
    from ..documents.base import NormalizedDocument

# Canonical type a speech extractor is allowed to process. Extraction below
# this type is skipped by the store integration helper, so a decision, a
# statement or a report document is never mistakenly mined for speech facts.
# Interviews (``interview``) are a separate publication type with their own
# treatment — out of Phase 11 scope.
SPEECH_PUBLICATION_TYPES = ("speech",)


class SpeechExtractor(ABC):
    """Extracts structured Facts from one speech ``NormalizedDocument``.

    Concrete extractors are pure: ``extract`` reads the document and returns an
    ``ExtractionResult``. Persistence is the caller's concern
    (``extract_speech`` / ``Store``), keeping extraction auditable and
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


def extract_speech(
    store,
    publication: Publication,
    *,
    document=None,
    extractor=None,
    expected_types: tuple[str, ...] = SPEECH_PUBLICATION_TYPES,
) -> list[ExtractionResult]:
    """Run the speech extractor for ``publication`` and persist its Facts.

    ``store`` is the existing SQLite ``Store``. The normalized document(s) come
    from the store unless a specific ``document`` is given. Facts are persisted
    with ``Store.rebuild_facts_for_document`` (delete + insert), so re-running
    never leaves stale rows.

    Offline by construction: nothing here touches the network.

    The current extraction result is the source of truth for the persisted
    state: ``rebuild_facts_for_document`` (delete + insert) runs for every
    valid normalized document, **including when the result is empty**, so a
    re-extraction that now yields no facts clears the stale facts of that
    document instead of leaving them behind (same guarantee as Phases 5–10).

    Returns the list of ``ExtractionResult`` (empty if no extractor applies or
    the publication is not classified as ``speech``).
    """
    extractor = extractor or get_extractor(publication.central_bank)
    if extractor is None:
        return []
    if not _is_speech_publication(store, publication, expected_types=expected_types):
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


def extract_speech_batch(store, *, bank: str | None = None) -> list[ExtractionResult]:
    """Run ``extract_speech`` over all stored publications (optionally one
    ``bank``). Returns the aggregated results."""
    publications = store.list_publications(bank=bank)
    results: list[ExtractionResult] = []
    for publication in publications:
        if not publication.id:
            continue
        results.extend(extract_speech(store, publication))
    return results


def _is_speech_publication(store, publication, *, expected_types: tuple[str, ...]) -> bool:
    """Gate extraction to speech publications.

    The ``classifications`` table is the **single source of truth** (same strict
    mechanism as Phases 5–10): extraction is authorized only when an
    authoritative classification record exists for the publication and its
    ``publication_type`` is ``speech``. The denormalized
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


from .ecb import EcbSpeechExtractor  # noqa: E402  (see below)

_EXTRACTORS: dict[str, SpeechExtractor] = {
    EcbSpeechExtractor.bank: EcbSpeechExtractor(),
}


def get_extractor(bank: str) -> SpeechExtractor | None:
    return _EXTRACTORS.get(bank)
