"""Fed — Summary of Economic Projections (SEP) extractor (Phase 4.5).

Extracts the official numerical economic projections of the Federal Open Market
Committee from the normalized document **tables**, answering "what does the FOMC
project for each variable and each year?".

The SEP is the Fed's projections document and is **table-driven**, but with a
structure of its own: every variable (real GDP growth, the unemployment rate,
headline and core PCE inflation) is reported as a list of metrics — the median,
the central tendency range and the full range of participants' individual
projections. The Fed-specific parsing feature here is **median extraction**: a
projection cell is a Fact only when its row carries the explicit ``median``
metric. The ranges (e.g. ``2.0–2.5``) are never parsed into numbers — a range
has no single value and is never coerced into one (``UNKNOWN ≠ PROJECTION`` —
precision over recall, Phase 4.5).

The Fed's SEP also carries the **federal funds rate** projections — the median
of participants' individual projections of the appropriate level of the funds
rate under their monetary policy assumptions. Those are kept as
``policy_rate / projection`` facts (the SEP explicitly publishes them as its
own projections, before the next FOMC decision — they are the Committee's own
content, kept with their source row verbatim; the *decision* itself is Phase 4.1
territory and is not here).

Structure handled (DocumentTable):

- headers: ``["Variable", "Metric", "2026", "2027", "2028", "Longer run"]`` —
  the first two columns identify the row, the numeric columns are the years
  (``20xx``). The ``Longer run`` column is not a year and is ignored (Phase 4.5
  full-table handling is out of scope for this representative).
- rows: the variable label (exact canonical match, never a substring near-miss),
  the metric (``median`` only), then one numeric value per year.
- unit gate at table level: the table is only mined when its caption explicitly
  states ``Percent`` (the SEP's own unit). A table without an explicit unit is
  ignored as a whole — no unit is ever assumed.

Deliberately NOT extracted (Phase 4.5 boundary):

- interpretation (hawkish/dovish, which policy is "projected") — never;
- the ranges, the central tendency band, bootstrap / fan-chart text, the
  "longer run" column, prose ("Participants agreed that …"), methodology and
  disclaimer sections;
- the decision itself (rates, changes, effective date) — Phase 4.1.
"""

from __future__ import annotations

import re

from ..classification.base import Confidence
from ..documents.base import DocumentTable, NormalizedDocument
from ..facts import (
    METHOD_TABLE,
    ExtractionResult,
    Fact,
    FactLocation,
    FactPeriod,
    FactValue,
    LocationKind,
    PeriodKind,
    ValueKind,
    percentage,
)
from ..normalize import normalize_title
from .base import ProjectionsExtractor

EXTRACTION_VERSION = "9.1.0"

SUBJECT_INFLATION = "inflation"
SUBJECT_CORE_INFLATION = "core_inflation"
SUBJECT_GDP = "gdp"
SUBJECT_UNEMPLOYMENT = "unemployment"
SUBJECT_POLICY_RATE = "policy_rate"

PREDICATE_PROJECTION = "projection"

# --- variable recognition (row label) — exact canonical matching only ------
_VAR_GDP = frozenset({"change in real gdp", "real gdp", "real gdp growth"})
_VAR_UNEMPLOYMENT = frozenset({"unemployment rate"})
_VAR_PCE = frozenset({"headline pce inflation", "pce inflation", "pce inflation rate", "pce prices"})
_VAR_CORE_PCE = frozenset({"core pce inflation", "core pce prices", "core pce inflation rate"})
_VAR_FEDFUNDS = frozenset({"federal funds rate", "federal fund rate"})

_FOOTNOTE = re.compile(r"\s*(?:\(\d+\)|\[\d+\]|\d+\)|[*†‡]+)\s*$")


def _canonical_label(cell: str) -> str:
    raw = normalize_title(cell or "")
    raw = _FOOTNOTE.sub("", raw).strip()
    return " ".join(raw.split())


def _subject_of(label: str) -> str | None:
    t = _canonical_label(label)
    if t in _VAR_GDP:
        return SUBJECT_GDP
    if t in _VAR_UNEMPLOYMENT:
        return SUBJECT_UNEMPLOYMENT
    if t in _VAR_PCE:
        return SUBJECT_INFLATION
    if t in _VAR_CORE_PCE:
        return SUBJECT_CORE_INFLATION
    if t in _VAR_FEDFUNDS:
        return SUBJECT_POLICY_RATE
    return None


_YEAR_CELL = re.compile(r"^\s*(20[0-9]{2})\s*$")
_MEDIAN = re.compile(r"^\s*median\s*(?:\(\d+\))?\s*$", re.IGNORECASE)

_NO_DATA = {"", "-", "–", "—", "…", "..", ".", "n.a.", "n/a", "na", "nd"}


def _numeric(cell: str) -> float | None:
    raw = (cell or "").strip()
    if not raw:
        return None
    cleaned = _FOOTNOTE.sub("", raw).strip()
    if cleaned.lower() in _NO_DATA:
        return None
    cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _row_text(row: list[str]) -> str:
    return " | ".join(str(cell or "") for cell in row)


# --- unit gate (table level): the SEP's own "Percent" unit in the caption ----
_UNIT_PERCENT = re.compile(r"\bpercent\b|\bper\s?cent\b", re.IGNORECASE)
_UNIT_SHARE = re.compile(r"%\s*of\s*gdp\b|percentage\s*of\s*gdp\b", re.IGNORECASE)


def _table_unit(name: str) -> str | None:
    t = (name or "").strip().lower()
    if not t:
        return None
    if _UNIT_SHARE.search(t):
        return None
    if _UNIT_PERCENT.search(t):
        return "percent"
    return None


class FedSepExtractor(ProjectionsExtractor):
    bank = "fed"
    extraction_version = EXTRACTION_VERSION

    def extract(self, publication, document: NormalizedDocument) -> ExtractionResult:
        result = ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )
        if not document.tables:
            result.warnings.append("no_tables")
            return result

        extracted = False
        for index, table in enumerate(document.tables):
            emitted = self._extract_table(result, document, index, table)
            extracted = extracted or emitted

        if not extracted:
            result.warnings.append("no_projection_table")
        return result

    def _extract_table(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        table: DocumentTable,
    ) -> bool:
        if _table_unit(table.name) != "percent":
            return False

        # columns: row[0] = variable label, row[1] = metric (median /
        # central tendency / range), columns 2+ aligned to the year headers.
        year_cols: list[tuple[int, str]] = []
        for cidx, cell in enumerate(table.headers):
            if cidx == 0:
                continue  # variable label column
            match = _YEAR_CELL.match(cell or "")
            if match is not None:
                year_cols.append((cidx, match.group(1)))
        if len(table.headers) < 2 or not year_cols:
            return False
        metric_col = 1

        emitted = False
        for rindex, row in enumerate(table.rows):
            if len(row) < 2:
                continue
            if metric_col >= len(row):
                continue
            if not _MEDIAN.match(row[metric_col] or ""):
                continue  # only the median metric is mined
            subject = _subject_of(row[0]) if row else None
            if subject is None:
                continue
            row_text = _row_text(row)
            for cidx, year in year_cols:
                cell = row[cidx] if cidx < len(row) else ""
                value = _numeric(cell)
                if value is None:
                    continue
                result.add(
                    self._cell_fact(
                        result, document, index, rindex, cidx, row_text, cell,
                        subject, PREDICATE_PROJECTION,
                        percentage(value, source_text=(cell or "").strip()),
                        FactPeriod(PeriodKind.YEAR, year, label=(table.headers[cidx] or "").strip()),
                        _asof_qualifier(table.name),
                    )
                )
                emitted = True
        return emitted

    @staticmethod
    def _cell_fact(result, document, table_index: int, row_index: int, column_index: int,
                   row_text: str, cell: str, subject: str, predicate: str, value: FactValue,
                   period: FactPeriod, qualifier: str) -> Fact:
        return Fact(
            publication_id=result.publication_id,
            document_id=document.document_id,
            subject=subject,
            predicate=predicate,
            value=value,
            period=period,
            effective_date=None,
            source_location=FactLocation(
                LocationKind.TABLE, table=table_index, row=row_index, column=column_index
            ),
            source_text=row_text,
            extraction_method=METHOD_TABLE,
            extraction_version=EXTRACTION_VERSION,
            confidence=Confidence.HIGH,
            speaker=None,
            identity_qualifier=qualifier,
        )


def _asof_qualifier(name: str) -> str:
    """Keep the SEP meeting reference when the table caption names it (e.g.
    "…, September 2026"). Defaults to ``sep:current`` otherwise."""
    m = re.search(r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+20[0-9]{2}", name or "", re.IGNORECASE)
    if not m:
        return "sep:current"
    return f"sep:{m.group(0).replace(' ', '-').lower()}"


__all__ = [
    "EXTRACTION_VERSION",
    "SUBJECT_INFLATION",
    "SUBJECT_CORE_INFLATION",
    "SUBJECT_GDP",
    "SUBJECT_UNEMPLOYMENT",
    "SUBJECT_POLICY_RATE",
    "PREDICATE_PROJECTION",
    "FedSepExtractor",
]