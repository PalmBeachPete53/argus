"""ECB — Staff macroeconomic projections extractor (Phase 9).

Extracts the official staff projections for the euro area from the normalized
document **tables**, answering "what does the ECB staff project for each
variable and each year?":

- the **headline projection variables** — HICP inflation, HICP excluding
  energy and food, and real GDP growth — as explicitly stated in the projection
  tables, one Fact per (variable, year) cell;

- **explicit revisions** — when a table carries a ``Revisions vs {Month Year}``
  column block (the ECB publishes revisions *relative to the previous
  projections*, in percentage points), the stated revision deltas are kept as
  ``predicate = revision`` facts. A revision is **never computed**: the
  ``current − previous`` difference between two projection tables is never
  calculated or invented (Phase 9 boundary).

Projection tables are recognised structurally (``DocumentTable``):

- the **columns** are years (``20xx`` header cells);
- the **rows** are variables (the leading cell of each row);
- the **unit is identified at the table level**, from the table's own caption:
  real ECB projection tables state their unit in the title line (e.g.
  "(annual percentage changes)"). A table whose unit is missing, unknown or
  incompatible is **ignored as a whole** — a value is never assumed to be a
  percentage, and the unit of one table can never authorise the numbers of
  another table;
- a cell is a Fact **only when** it can be identified by a recognised variable
  (row label, matched as an exact canonical label) + a year (column header) +
  an explicit unit. A bare number without that identity — a header-less table,
  an unlabelled row, a scenario or assumption column — is never a Fact
  (``UNKNOWN ≠ PROJECTION``).

Only the three core variables are mined; any other row (private consumption,
unemployment, oil prices, exchange rates, …) is **ignored** — reliability over
coverage. Variable identification is by **exact canonical label matching**:
near-misses ("GDP deflator", "GDP per capita", "HICP excluding energy", …) are
never coerced into a core variable. In particular the "Technical assumptions"
box (oil price, exchange rates, interest rates) is a set of *assumptions*, not
projections, and yields nothing.

Deliberately NOT extracted (Phase 9 boundary):

- interpretation (hawkish/dovish, stance, market impact) — never;
- policy decisions / rates / guidance — Phases 5–8, gated on their own
  publication types;
- analysis of the outlook (prose), methodology, disclaimer / copyright /
  legal-notice sections — ignored;
- revisions **computed** as ``current − previous`` — never; only explicitly
  stated revision columns are kept;
- Phases 10–11 (reports, speeches) — not this layer.

Design rules

- No fact is invented. A value is only produced from an explicitly stated
  table cell, and every Fact preserves verbatim provenance: ``source_text`` is
  the exact row of the source table, ``value.source_text`` the exact cell, and
  ``source_location`` pinpoints ``table / row / column``.
- Units are kept explicitly: projection values are ``percentage`` (annual
  percentage changes), revision deltas are plain numbers with
  ``unit = "pp"`` (percentage points) — a ``0.4`` percentage-point revision is
  never silently turned into ``0.4%`` or basis points. A projection value is
  produced **only when** its table's unit is explicitly recognised as a
  percentage — never assumed from the value itself.
- Periods come from the **table headers** (``year:2026`` …), never guessed from
  text position.
- ``Fact.speaker`` is always ``None`` (projections are collective staff
  output, never attributed to an individual).
- Confidence is ``HIGH`` for every table-extracted value.
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
    number,
    percentage,
)
from ..normalize import normalize_title
from .base import ProjectionsExtractor

EXTRACTION_VERSION = "9.0.0"

# ---------------------------------------------------------------------------
# Canonical Phase 9 subjects (controlled vocabulary). Reuses the Phase 6/7/8
# subjects verbatim: a projection of headline HICP is the same ``inflation``
# subject, a projection of HICP excluding energy and food is ``core_inflation``,
# and a real GDP projection is ``gdp``. Only these three core variables are
# mined; any other table row is ignored (conservative).
# ---------------------------------------------------------------------------
SUBJECT_INFLATION = "inflation"
SUBJECT_CORE_INFLATION = "core_inflation"
SUBJECT_GDP = "gdp"

PREDICATE_PROJECTION = "projection"
PREDICATE_REVISION = "revision"

# ---------------------------------------------------------------------------
# Variable recognition (row labels). Identification is by **exact canonical
# label matching**: the row label is normalised and stripped of footnote
# markers, then compared against the controlled-vocabulary sets below. A label
# is a core variable only when it equals one of these entries verbatim —
# substring "near-misses" ("GDP deflator", "GDP per capita", "HICP excluding
# energy", …) never match, so they are ignored rather than coerced.
# ---------------------------------------------------------------------------
_VAR_CORE = frozenset(
    {"hicp excluding energy and food", "hicpx", "core hicp", "core inflation"}
)
_VAR_HICP = frozenset({"hicp", "hicp inflation"})
_VAR_GDP = frozenset({"real gdp", "gdp growth", "real gdp growth"})

_FOOTNOTE = re.compile(r"\s*(?:\(\d+\)|\[\d+\]|\d+\)|[*†‡]+)\s*$")


def _canonical_label(cell: str) -> str:
    """Normalise a row label: strip footnote markers, collapse whitespace,
    lowercase. Empty string when the label is empty or pure markers."""
    raw = normalize_title(cell or "")
    raw = _FOOTNOTE.sub("", raw).strip()
    raw = " ".join(raw.split())
    return raw


def _subject_of(label: str) -> str | None:
    t = _canonical_label(label)
    if not t:
        return None
    if t in _VAR_CORE:
        return SUBJECT_CORE_INFLATION
    if t in _VAR_HICP:
        return SUBJECT_INFLATION
    if t in _VAR_GDP:
        return SUBJECT_GDP
    return None


# ---------------------------------------------------------------------------
# Header parsing: year columns and explicit "Revisions vs {Month Year}" blocks.
# ---------------------------------------------------------------------------
_YEAR_CELL = re.compile(r"^\s*(20[0-9]{2})\s*$")
_MONTH_WORDS = "january|february|march|april|may|june|july|august|september|october|november|december"
_MONTH_NUM = {
    "january": "01", "february": "02", "march": "03", "april": "04", "may": "05", "june": "06",
    "july": "07", "august": "08", "september": "09", "october": "10", "november": "11", "december": "12",
}
_REVISION_REF = re.compile(
    rf"revisions?\s+vs\.?\s+(?P<month>{_MONTH_WORDS})\s+(?P<year>20[0-9]{{2}})", re.IGNORECASE
)
_ASOF_MONTH = re.compile(rf"(?P<month>{_MONTH_WORDS})\s+(?P<year>20[0-9]{{2}})", re.IGNORECASE)


def _yyyymm(month: str, year: str) -> str:
    return f"{year}-{_MONTH_NUM[month.lower()]}"


def _revision_ref(cell: str) -> str | None:
    """Return ``YYYY-MM`` when ``cell`` is an explicit "Revisions vs Month
    Year" column-block label, else None."""
    m = _REVISION_REF.search(cell or "")
    if not m:
        return None
    return _yyyymm(m.group("month"), m.group("year"))


def _asof_ref(name: str) -> str | None:
    """Return ``YYYY-MM`` when the table name/caption carries an explicit
    as-of month+year (e.g. "March 2026 projections"), else None."""
    m = _ASOF_MONTH.search(name or "")
    if not m:
        return None
    return _yyyymm(m.group("month"), m.group("year"))


# ---------------------------------------------------------------------------
# Cell parsing: a numeric value, tolerant of verbatim footnote markers, and
# explicit about "no data" placeholders. A non-numeric cell is skipped, never
# coerced.
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Unit gate (table level). A projection Fact is only produced from a table
# whose caption explicitly states a percentage unit. The unit is read from the
# table's own caption — never inferred from the values and never borrowed from
# another table. The checks run in this order:
#
#   1. *share* units (``% of GDP``, ``percentage of total``, …) are rejected
#      first — those are ratios, not percentage *changes*;
#   2. percentage markers are then matched (``annual percentage changes``,
#      ``percent``/``per cent`` as whole words, ``% growth``, ``(%)``, …) so
#      the real ECB caption "(annual percentage changes; revisions in
#      percentage points)" still authorises the table;
#   3. incompatible units (index, points, currencies, energy units, …) are
#      rejected.
#
# A missing, unknown or incompatible unit means the whole table is ignored:
# no unit is ever assumed. ``\bpercent\b`` (whole word) deliberately does not
# match "percentage", so "(percentage points)" alone never authorises a table.
# ---------------------------------------------------------------------------
_UNIT_SHARE = (
    r"%\s*of\s*gdp",
    r"percentage\s*of\s*gdp",
    r"\bper\s?cent\s*of\s*gdp\b",
    r"%\s*of\s*total",
    r"%\s*of\s*disposable\s*income",
)

_UNIT_PERCENTAGE = (
    r"annual\s+percentage\s+changes?",
    r"percentage\s+changes?",
    r"annual\s+growth\s+rates?",
    r"\bper\s?cent\b",
    r"\bpercent\b",
    r"%\s*changes?",
    r"%\s*growth",
    r"\(\s*%\s*\)",
)

_UNIT_INCOMPATIBLE = (
    r"\bindex\b",
    r"\bpoints\b",
    r"\busd\b",
    r"\bus\$\b",
    r"\beur\b",
    r"\beuro\b",
    r"\bmwh\b",
    r"\bkwh\b",
    r"\btonnes?\b",
    r"\bbarrel\b",
    r"\$\b",
    r"€",
)


def _table_unit(name: str) -> str | None:
    """Return ``"percentage"`` when the table caption explicitly states a
    percentage unit, else ``None`` (missing, unknown or incompatible)."""
    t = (name or "").strip().lower()
    if not t:
        return None
    if any(re.search(marker, t) for marker in _UNIT_SHARE):
        return None
    if any(re.search(marker, t) for marker in _UNIT_PERCENTAGE):
        return "percentage"
    if any(re.search(marker, t) for marker in _UNIT_INCOMPATIBLE):
        return None
    return None


class EcbProjectionsExtractor(ProjectionsExtractor):
    bank = "ecb"
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
        """Mine one projection table. Returns True when at least one Fact was
        emitted from it."""
        # --- columns: value years first, then explicit revision years ---
        value_cols: list[tuple[int, str]] = []  # (column index, year)
        revision_cols: list[tuple[int, str, str]] = []  # (column index, year, reference yyyymm)
        revision_ref: str | None = None
        for cidx, cell in enumerate(table.headers):
            ref = _revision_ref(cell)
            if ref is not None:
                revision_ref = ref
                continue
            match = _YEAR_CELL.match(cell or "")
            if match is None:
                continue
            if revision_ref is not None:
                revision_cols.append((cidx, match.group(1), revision_ref))
            else:
                value_cols.append((cidx, match.group(1)))

        if not value_cols:
            return False  # no projection year columns → not a projection table

        if _table_unit(table.name) != "percentage":
            return False  # no explicit percentage unit → not a projection table

        asof = _asof_ref(table.name)
        projection_qualifier = f"projections:{asof}" if asof else "projections:current"

        emitted = False
        for rindex, row in enumerate(table.rows):
            subject = _subject_of(row[0]) if row else None
            if subject is None:
                continue
            row_text = _row_text(row)
            for cidx, year in value_cols:
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
                        projection_qualifier,
                    )
                )
                emitted = True
            for cidx, year, ref in revision_cols:
                cell = row[cidx] if cidx < len(row) else ""
                value = _numeric(cell)
                if value is None:
                    continue
                result.add(
                    self._cell_fact(
                        result, document, index, rindex, cidx, row_text, cell,
                        subject, PREDICATE_REVISION,
                        number(value, unit="pp", source_text=(cell or "").strip()),
                        FactPeriod(PeriodKind.YEAR, year, label=(table.headers[cidx] or "").strip()),
                        f"projections:revision_vs:{ref}",
                    )
                )
                emitted = True
        return emitted

    @staticmethod
    def _cell_fact(
        result: ExtractionResult,
        document: NormalizedDocument,
        table_index: int,
        row_index: int,
        column_index: int,
        row_text: str,
        cell: str,
        subject: str,
        predicate: str,
        value: FactValue,
        period: FactPeriod,
        qualifier: str,
    ) -> Fact:
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