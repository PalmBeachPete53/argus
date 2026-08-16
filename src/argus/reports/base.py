"""Phase 4.6 — type-specific monetary policy report extractors.

An extractor converts a ``NormalizedDocument`` of a monetary policy report (the
ECB/Eurosystem "Economic Bulletin" being the report-like publication of the
euro area) into a list of provenance-carrying ``Fact`` assertions, following
the ``ExtractionResult`` contract defined in Phase 4 (``facts/base.py``).

Architecture:
    NormalizedDocument
        ↓ ReportsExtractor.extract(publication, document)
    ExtractionResult(publication_id, document_id, facts, warnings)
        ↓ Store.rebuild_facts_for_document
    facts table

Extractors are per-bank (bank-specific wording and section labels are
encapsulated, invariant 10); the generic engine in this module only dispatches
on ``central_bank``.

A monetary policy report is a large **narrative** document: economic outlook,
inflation drivers, growth outlook, labour market, financial conditions, risks
and policy rationale. Phase 4.6 is the most over-extraction-prone phase so far,
so its cardinal rule is **precision over recall**: a Fact is only produced from
a known economic section (conservative routing) + an explicit economic
assertion with sufficient identity (subject + predicate + value + unit + period
when applicable) + provenance. An unknown section — even one full of
economic-looking sentences — is never mined (``UNKNOWN ≠ ECONOMIC``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..facts import ExtractionResult

if TYPE_CHECKING:
    from ..models import Publication
    from ..documents.base import NormalizedDocument

# Canonical type a monetary policy report extractor is allowed to process.
# Extraction below this type is skipped by the store integration helper, so a
# decision, a statement or a projections document is never mistakenly mined for
# report facts.
REPORT_PUBLICATION_TYPES = ("monetary_policy_report",)


class ReportsExtractor(ABC):
    """Extracts structured Facts from one monetary policy report
    ``NormalizedDocument``.

    Concrete extractors are pure: ``extract`` reads the document and returns an
    ``ExtractionResult``. Persistence is the caller's concern
    (``extract_report`` / ``Store``), keeping extraction auditable and
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


def extract_report(
    store,
    publication: Publication,
    *,
    document=None,
    extractor=None,
    expected_types: tuple[str, ...] = REPORT_PUBLICATION_TYPES,
) -> list[ExtractionResult]:
    """Run the monetary policy report extractor for ``publication`` and persist
    its Facts.

    ``store`` is the existing SQLite ``Store``. The normalized document(s) come
    from the store unless a specific ``document`` is given. Facts are persisted
    with ``Store.rebuild_facts_for_document`` (delete + insert), so re-running
    never leaves stale rows.

    Offline by construction: nothing here touches the network.

    The current extraction result is the source of truth for the persisted
    state: ``rebuild_facts_for_document`` (delete + insert) runs for every
    valid normalized document, **including when the result is empty**, so a
    re-extraction that now yields no facts clears the stale facts of that
    document instead of leaving them behind (same guarantee as Phases 4.1–4.5).

    Returns the list of ``ExtractionResult`` (empty if no extractor applies or
    the publication is not classified as ``monetary_policy_report``).
    """
    extractor = extractor or get_extractor(publication.central_bank)
    if extractor is None:
        return []
    if not _is_report_publication(store, publication, expected_types=expected_types):
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


def extract_report_batch(store, *, bank: str | None = None) -> list[ExtractionResult]:
    """Run ``extract_report`` over all stored publications (optionally one
    ``bank``). Returns the aggregated results."""
    publications = store.list_publications(bank=bank)
    results: list[ExtractionResult] = []
    for publication in publications:
        if not publication.id:
            continue
        results.extend(extract_report(store, publication))
    return results


def _is_report_publication(store, publication, *, expected_types: tuple[str, ...]) -> bool:
    """Gate extraction to monetary policy report publications.

    The ``classifications`` table is the **single source of truth** (same strict
    mechanism as Phases 4.1–4.5): extraction is authorized only when an
    authoritative classification record exists for the publication and its
    ``publication_type`` is ``monetary_policy_report``. The denormalized
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


from .ecb import EcbReportsExtractor  # noqa: E402  (see below)
from .boe import BoeReportExtractor  # noqa: E402
from .boc import BocReportExtractor  # noqa: E402
from .rba import RbaReportExtractor  # noqa: E402
from .rbnz import RbnzReportExtractor  # noqa: E402
from .riksbank import RiksbankReportExtractor  # noqa: E402

_EXTRACTORS: dict[str, ReportsExtractor] = {
    EcbReportsExtractor.bank: EcbReportsExtractor(),
    BoeReportExtractor.bank: BoeReportExtractor(),
    BocReportExtractor.bank: BocReportExtractor(),
    RbaReportExtractor.bank: RbaReportExtractor(),
    RbnzReportExtractor.bank: RbnzReportExtractor(),
    RiksbankReportExtractor.bank: RiksbankReportExtractor(),
}


def get_extractor(bank: str) -> ReportsExtractor | None:
    return _EXTRACTORS.get(bank)


from .norges import NorgesReportExtractor  # noqa: E402

if NorgesReportExtractor.bank not in _EXTRACTORS:
    _EXTRACTORS[NorgesReportExtractor.bank] = NorgesReportExtractor()
