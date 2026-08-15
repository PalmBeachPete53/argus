"""BoJ — Outlook for Economic Activity and Prices extractor (Phase 9).

Extracts the official numerical projections of the Bank of Japan's Policy Board
from the normalized document **tables**, answering "what does the Policy Board
project for each variable and each fiscal year?".

The "Outlook for Economic Activity and Prices" is the BoJ's projections
document and is **table-driven**: the Board publishes a summary table of its
forecasts of real GDP growth, the CPI and related price measures for the current
fiscal year (FY) and the next two fiscal years. A projection cell is a Fact only
when its row carries a recognised variable label, its column is a fiscal year
(``20xx`` or ``FY20xx``), and the table's caption states an explicit percentage
unit (typically "… year-on-year percentage change …").

Structure handled (DocumentTable):

- headers: the leading column(s) identify the variable (consolidated in the
  first header cell), the numeric columns are fiscal years (``20xx`` /
  ``FY20xx``, parsed to the bare year);
- rows: the variable label (exact canonical match, never a substring near-miss),
  then one numeric value per year;
- unit gate at table level: the table is only mined when its caption explicitly
  states a percentage unit. A table without an explicit unit is ignored as a
  whole — no unit is ever assumed.

Variables mined: real GDP (→ ``gdp``), the CPI / "consumer price index"
(→ ``inflation``), the core CPI / "excluding fresh food" (→ ``core_inflation``)
and, when published, the unemployment rate (→ ``unemployment``). Any other row
is ignored (precision over recall, Phase 9).

Deliberately NOT extracted (Phase 9 boundary):

- interpretation (hawkish/dovish, which policy is "projected") — never;
- the ranges / fan-chart text, prose ("The Policy Board discussed …"),
  methodology and disclaimer sections;
- the decision / statement content (rates, guidance) — Phases 5/6;
- the minutes — Phase 8.
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

EXTRACTION_VERSION = "9.2.0"

SUBJECT_INFLATION = "inflation"
SUBJECT_CORE_INFLATION = "core_inflation"
SUBJECT_GDP = "gdp"
SUBJECT_UNEMPLOYMENT = "unemployment"

PREDICATE_PROJECTION = "projection"

# --- variable recognition (row label) — exact canonical matching only ------
_VAR_GDP = frozenset({"real gdp", "change in real gdp", "real gdp growth", "real gdp growth rate"})
_VAR_UNEMPLOYMENT = frozenset({"unemployment rate", "rate of unemployment"})
_VAR_CPI = frozenset({
    "consumer price index", "consumer prices", "cpi", "cpi (all items)",
    "consumer price index (all items)",
})
_VAR_CORE_CPI = frozenset({
    "cpi excluding fresh food",
    "consumer prices excluding fresh food",
    "consumer price index excluding fresh food",
    "consumer price index (excluding fresh food)",
    "cpi (excluding fresh food)",
    "cpi less fresh food",
    "core cpi",
})

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
    if t in _VAR_CPI:
        return SUBJECT_INFLATION
    if t in _VAR_CORE_CPI:
        return SUBJECT_CORE_INFLATION
    return None


# A fiscal-year column header: "2026", "FY2026", "FY 2026".
_YEAR_CELL = re.compile(r"^\s*(?:fy)?\s*(20[0-9]{2})\s*$", re.IGNORECASE)

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


# --- unit gate (table level): an explicit percentage unit in the caption ----
_UNIT_PERCENT = re.compile(r"\bpercent\b|\bper\s?cent\b|percentage\b|% growth\b", re.IGNORECASE)
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


def _asof_qualifier(name: str) -> str:
    """Keep the Outlook reference when the table caption names the meeting / a
    month (e.g. "…, April 2026"). Defaults to ``outlook:current`` otherwise."""
    m = re.search(
        r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+20[0-9]{2}",
        name or "", re.IGNORECASE,
    )
    if not m:
        return "outlook:current"
    return f"outlook:{m.group(0).replace(' ', '-').lower()}"


class BojProjectionsExtractor(ProjectionsExtractor):
    bank = "boj"
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

        year_cols: list[tuple[int, str]] = []
        for cidx, cell in enumerate(table.headers):
            if cidx == 0:
                continue  # variable label column
            match = _YEAR_CELL.match(cell or "")
            if match is not None:
                year_cols.append((cidx, match.group(1)))
        if len(table.headers) < 1 or not year_cols:
            return False

        emitted = False
        for rindex, row in enumerate(table.rows):
            if not row:
                continue
            subject = _subject_of(row[0])
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


__all__ = [
    "EXTRACTION_VERSION",
    "SUBJECT_INFLATION",
    "SUBJECT_CORE_INFLATION",
    "SUBJECT_GDP",
    "SUBJECT_UNEMPLOYMENT",
    "PREDICATE_PROJECTION",
    "BojProjectionsExtractor",
]
