"""ECB — Monetary Policy Report extractor (Phase 10).

Extracts the facts of an ECB monetary policy report (the euro area's
report-like publication being the ECB "Economic Bulletin") from the normalized
document, answering "what does the report explicitly state about the economy
and about monetary policy?":

- the **economic outlook** — inflation (``inflation`` / ``core_inflation`` /
  ``inflation_expectations``), growth (``growth`` qualitative, ``gdp``
  quantitative), the labour market (``labour_market`` / ``unemployment`` /
  ``wages``), financial conditions and fiscal developments, as verbatim
  assessments and as explicit quantitative value claims;
- the **risk assessment**, as categorical orientations (``upside`` /
  ``downside`` / ``balanced``) only when explicitly stated, otherwise verbatim
  text;
- explicit **policy statements** and **forward guidance**, verbatim, never
  interpreted.

Phase 10 is the most over-extraction-prone phase: a monetary policy report is a
large narrative document full of economic language. Its cardinal rule is
**precision over recall** — ``known section + explicit assertion + sufficient
identity + provenance → Fact``, and ``unknown section + economic-looking
content → IGNORE``.

Sections are routed conservatively by heading: a known economic heading is
mined, and a known non-economic heading (foreword, editorial, legal notice,
statistics, annexes, methodology, contents, the report title) — or an **unknown
heading** — is ignored ("absence of proof → absence of extraction"). Analytical
**boxes** ("Box 1 — …") are deliberately ignored: they are interpretive essays
and their content is not mined, protecting precision. Content is then
classified sentence-by-sentence with a fixed precedence (guidance > policy >
risk > financial > inflation > labour > growth > fiscal); the heading only
gates whether the section is mined at all.

Deliberately NOT extracted (Phase 10 boundary):

- hawkish/dovish, bullish/bearish or any market interpretation — never
- policy decisions/rates/votes — Phases 5–8, gated on their own publication
  types; the report's *narrative* of policy is kept verbatim, never priced
- the structured economic projections tables — Phase 9, gated on
  ``economic_projections``; prose forecasts inside a report are kept as value
  claims only when they carry an explicit reference period
- Phases 11 (speeches) — not this layer.

Design rules

- No fact is invented. A value/orientation is only produced when the source
  states it, and every Fact preserves an *exact verbatim* supporting passage
  (``source_text``) copied from the normalized document.
- **Units are explicit.** Values are kept as ``percentage`` only when the
  sentence states a percentage; share/ratio units ("% of GDP", "% of total")
  are never converted into percentage facts, and a forecast value claim
  without an explicit reference period is under-determined and ignored.
- **Periods** come from the sentence wording (year, month, quarter) — never
  guessed from proximity. ``Fact.effective_date`` is always ``None``
  (distinct from the publication date and the reference period).
- Risk facts are categorical (``upside`` / ``downside`` / ``balanced``) only
  when the source states an explicit orientation; otherwise they are verbatim
  text assessments ("risks remained elevated" is never forced into a category).
- The same assertion stated twice (e.g. in an overview and in the detailed
  section, in prose and in a table) is emitted **once**: exact-verbatim
  (and exact value+period) duplicates are suppressed within a run, so no
  fact is invented twice for one piece of content.
- ``Fact.speaker`` is always ``None``: a monetary policy report is a
  collective institutional publication; "the Governing Council" is never
  turned into an individual.
- Confidence is ``HIGH`` for quantitative percentages and categorical risk
  orientations (explicit source wording); ``MEDIUM`` for verbatim qualitative
  assessments.
"""

from __future__ import annotations

import re

from ..classification.base import Confidence
from ..documents.base import NormalizedDocument
from ..facts import (
    METHOD_REGEX,
    METHOD_TABLE,
    ExtractionResult,
    Fact,
    FactLocation,
    FactPeriod,
    FactValue,
    LocationKind,
    PeriodKind,
    ValueKind,
    categorical,
    percentage,
)
from ..normalize import normalize_title
from .base import ReportsExtractor

EXTRACTION_VERSION = "10.0.0"

# ---------------------------------------------------------------------------
# Canonical Phase 10 subjects (controlled vocabulary, see docs/EXTRACTORS.md).
# Reuses the Phase 6/7/8 subjects verbatim; ``fiscal_policy`` is added for the
# fiscal developments section.
# ---------------------------------------------------------------------------
SUBJECT_INFLATION = "inflation"
SUBJECT_CORE_INFLATION = "core_inflation"
SUBJECT_INFLATION_EXPECTATIONS = "inflation_expectations"
SUBJECT_GROWTH = "growth"
SUBJECT_GDP = "gdp"
SUBJECT_LABOUR_MARKET = "labour_market"
SUBJECT_UNEMPLOYMENT = "unemployment"
SUBJECT_WAGES = "wages"
SUBJECT_FINANCIAL_CONDITIONS = "financial_conditions"
SUBJECT_RISK = "risk"
SUBJECT_INFLATION_RISK = "inflation_risk"
SUBJECT_GROWTH_RISK = "growth_risk"
SUBJECT_MONETARY_POLICY = "monetary_policy"
SUBJECT_POLICY_GUIDANCE = "policy_guidance"
SUBJECT_FISCAL_POLICY = "fiscal_policy"

PREDICATE_ASSESSMENT = "assessment"
PREDICATE_STATEMENT = "statement"
PREDICATE_VALUE = "value"

# ---------------------------------------------------------------------------
# Section routing — CONSERVATIVE (Phase 10). A heading is mined only when it is
# a known economic section; an unknown heading and the known non-economic ones
# (title, foreword, legal notice, statistics, annexes, methodology, contents,
# boxes) are ignored. "Absence of proof → absence of extraction": an unknown
# future section — even one full of economic-looking sentences — never yields a
# fact (``UNKNOWN ≠ ECONOMIC``).
# ---------------------------------------------------------------------------
CAT_IGNORE = "ignore"
CAT_UNKNOWN = "unknown"
CAT_GENERAL = "general"
CAT_POLICY = "policy"
CAT_RISK = "risk"
CAT_FINANCIAL = "financial"
CAT_INFLATION = "inflation"
CAT_LABOUR = "labour"
CAT_GROWTH = "growth"
CAT_FISCAL = "fiscal"

# Non-economic headings are matched EXACTLY on the normalized heading, exactly
# like the economic headings: a heading is ignored only when it IS one of these
# controlled headings. Substring coincidence never determines identity —
# "annexation of …" is never "annex", "legal framework …" is never "legal
# notice", "statistical outlook" is never "statistical annex". Every supported
# variant must be listed explicitly.
_IGNORE_HEADINGS = frozenset({
    # report masthead / title
    "economic bulletin",
    "monetary policy report",
    # front matter
    "foreword",
    "editorial",
    "contents",
    "acknowledgements",
    "abbreviations",
    # legal & disclaimers
    "legal notice",
    "disclaimer",
    "copyright",
    "imprint",
    # back matter
    "glossary",
    "references",
    "bibliography",
    "appendix",
    "technical appendix",
    "statistics",
    "statistical annex",
    "annex",
    "methodology",
    "note",
})
# Headings are matched EXACTLY on the normalized heading (lowercased,
# de-numbered, de-footnoted, leading "the" and trailing punctuation removed).
# Substring routing is intentionally gone: "Non-economic developments" must
# never be read as "economic", "Risk management" never as "risk", "Fiscal
# institutions" never as "fiscal", "Employment policy" never as "employment".
# Only these controlled, known headings mark a section as mined; any other
# heading — even one full of economic-looking sentences — is ignored
# (``UNKNOWN ≠ ECONOMIC``).
_POLICY_HEADINGS = frozenset({
    "monetary policy developments", "monetary policy", "policy considerations",
    "policy decisions", "policy stance", "monetary policy stance",
})
_RISK_HEADINGS = frozenset({"risk assessment", "risks", "risk"})
_INFLATION_HEADINGS = frozenset({"prices and costs", "price developments", "inflation"})
_LABOUR_HEADINGS = frozenset({
    "labour market", "labor market", "employment", "wages", "wage developments",
})
_FINANCIAL_HEADINGS = frozenset({
    "financial developments", "financial conditions", "financial markets",
    "money and credit", "monetary and financial", "financial system",
})
_GROWTH_HEADINGS = frozenset({
    "economic activity", "real economy", "growth", "output",
    "economic outlook", "euro area economy",
})
_FISCAL_HEADINGS = frozenset({"fiscal developments", "fiscal policy", "fiscal", "public finances"})
_GENERAL_HEADINGS = frozenset({
    "external environment", "international environment", "economic and monetary developments",
    "economic analysis", "overview", "summary", "executive summary", "world economy",
    "global economy", "economic",
})

_FOOTNOTE_MARK = re.compile(r"\s*(?:\(\d+\)|\[\d+\]|\d+\)|[*†‡]+)\s*$")
_BOX_PREFIX = re.compile(r"^\s*box\b", re.IGNORECASE)
_LEADING_NUM = re.compile(r"^\s*(?:[0-9]+(?:\.[0-9]+)*)\s*[-–—.:]?\s*")
_LEADING_THE = re.compile(r"^the\s+")
_TRAILING_PUNCT = re.compile(r"[\s.:;,\-–—]+$")


def _section_category(heading: str) -> str:
    """Route a section by its normalized heading.

    Returns ``CAT_IGNORE`` (a known non-economic controlled heading or a
    heading-less section / analytical box), ``CAT_UNKNOWN`` (a heading that is
    neither a controlled non-economic heading nor a controlled economic
    heading — ``UNKNOWN ≠ ECONOMIC``), or a mined economic category label.
    Only the controlled exact headings in the sets above are ever categorized;
    substring coincidence never determines identity. The label does not
    constrain the per-sentence classification (content-first), it only marks
    the section as mined.
    """
    t = normalize_title(heading or "")
    if not t:
        return CAT_IGNORE
    if _BOX_PREFIX.match(t):
        return CAT_IGNORE  # analytical boxes are never mined
    t = _LEADING_NUM.sub("", t).strip()
    t = _FOOTNOTE_MARK.sub("", t).strip()
    t = _LEADING_THE.sub("", t).strip()
    t = _TRAILING_PUNCT.sub("", t).strip()
    if t in _IGNORE_HEADINGS:
        return CAT_IGNORE
    if t in _POLICY_HEADINGS:
        return CAT_POLICY
    if t in _RISK_HEADINGS:
        return CAT_RISK
    if t in _INFLATION_HEADINGS:
        return CAT_INFLATION
    if t in _LABOUR_HEADINGS:
        return CAT_LABOUR
    if t in _FINANCIAL_HEADINGS:
        return CAT_FINANCIAL
    if t in _GROWTH_HEADINGS:
        return CAT_GROWTH
    if t in _FISCAL_HEADINGS:
        return CAT_FISCAL
    if t in _GENERAL_HEADINGS:
        return CAT_GENERAL
    return CAT_UNKNOWN


# ---------------------------------------------------------------------------
# Category anchors, content-first. Fixed precedence: guidance (G) > policy
# stance (D) > risks (E) > financial conditions (F) > inflation (A) > labour
# market (C) > growth (B) > fiscal.
# ---------------------------------------------------------------------------
CAT_NONE = "none"
CAT_GUIDANCE = "guidance"

_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(?:stand|stands|standing|stood)\s+ready\s+to\b", re.IGNORECASE),
    re.compile(r"\bwould\s+be\s+prepared\s+to\b", re.IGNORECASE),
    re.compile(r"\bwill\s+not\s+hesitate\s+to\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bwill\s+keep\s+(?:the\s+)?(?:key\s+)?(?:ecb\s+)?interest\s+rates?\b", re.IGNORECASE),
    re.compile(r"\bexpects?\s+(?:the\s+)?(?:key\s+)?(?:ecb\s+)?interest\s+rates?\s+to\s+remain\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
    re.compile(r"\b(?:future\s+)?(?:policy\s+)?(?:decisions?|rates?)\s+(?:will|would)\s+depend\s+on\b", re.IGNORECASE),
    re.compile(r"\bdata-?dependent\b", re.IGNORECASE),
    re.compile(r"\bwill\s+not\s+pre-?commit\b", re.IGNORECASE),
    re.compile(r"\bfuture\s+(?:policy\s+)?decisions\b", re.IGNORECASE),
    re.compile(r"\bpolicy\s+path\b", re.IGNORECASE),
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess)\b", re.IGNORECASE),
    re.compile(r"\bmeeting\s+by\s+meeting\b", re.IGNORECASE),
)

# D — policy stance/decisions. Compound signal (a stance word AND a policy
# term) so "the growth trajectory" is never mined as policy; plus the
# stance-phrase pattern for unambiguous "accommodative/restrictive stance".
# The bare tokens "policy", "rate"/"rates" are deliberately absent — generic
# single words ("policy implementation", "policy rate of change") are not a
# policy signal; the term must be monetary-policy specific.
_POLICY_TERM = re.compile(
    r"\b(?:"
    r"monetary(?:\s+policy)?"
    r"|monetary\s+policy\s+stance"
    r"|interest\s+rates?"
    r"|key\s+ecb\s+interest\s+rates?"
    r"|policy\s+rates?"
    r"|governing\s+council"
    r"|council"
    r"|eurosystem"
    r"|(?:key\s+)?policy\s+instruments?"
    r"|instruments?\s+within\s+(?:its|the)\s+mandate"
    r")\b",
    re.IGNORECASE,
)
_POLICY_STANCE = re.compile(
    r"\bstance\b"
    r"|\bdecided\s+to\b"
    r"|\bdecision(?:s)?\b"
    r"|\bpre-?commit(?:ment|ting)?\b"
    r"|\bcommitted\s+to\b"
    r"|\bappropriate\b"
    r"|\brestrictive\b"
    r"|\baccommodative\b"
    r"|\btightening\b"
    r"|\beasing\b",
    re.IGNORECASE,
)
_POLICY_STANCE_PHRASE = re.compile(
    r"\b(?:monetary\s+policy\s+stance|policy\s+stance)\b"
    r"|\b(?:appropriate|restrictive|accommodative|neutral|loose)\s+stance\s+of\s+(?:monetary\s+)?policy\b"
    r"|\bstance\s+of\s+(?:monetary\s+)?policy\b",
    re.IGNORECASE,
)


def _is_policy_sentence(sentence: str) -> bool:
    if bool(_POLICY_STANCE.search(sentence) and _POLICY_TERM.search(sentence)):
        return True
    return bool(_POLICY_STANCE_PHRASE.search(sentence))


_RISK_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\brisks?\s+(?:to|for|around|surrounding|from|of|are|were|remain|remained|have|has)\b", re.IGNORECASE),
    re.compile(r"\b(?:downside|upside|two-sided|symmetric|broadly\s+balanced)\s+risks?\b", re.IGNORECASE),
    re.compile(r"\buncertain(?:ty|ties)?\b", re.IGNORECASE),
    re.compile(r"\btilted\b", re.IGNORECASE),
)

_FINANCIAL_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bfinancial conditions\b", re.IGNORECASE),
    re.compile(r"\bfinancing conditions?\b", re.IGNORECASE),
    # "credit" is kept intentionally: in a monetary policy report the word
    # reliably denotes credit creation/conditions ("non-financial corporations"
    # is never caught because it carries no bare "credit" token).
    re.compile(r"\bcredit\b", re.IGNORECASE),
    re.compile(r"\bbank lending\b", re.IGNORECASE),
    re.compile(r"\b(?:bank\s+lending|lending\s+(?:rates?|growth|to|conditions?))\b", re.IGNORECASE),
    re.compile(r"\b(?:yield|credit|sovereign|bond|rate)\s+spreads?\b", re.IGNORECASE),
    re.compile(r"\bmonetary\s+policy\s+transmission\b", re.IGNORECASE),
    re.compile(r"\bmarket rates?\b", re.IGNORECASE),
    re.compile(r"\bborrowing costs?\b", re.IGNORECASE),
    re.compile(r"\bbond markets?\b", re.IGNORECASE),
    re.compile(r"\b(?:funding\s+(?:conditions?|costs?|markets?|constraints?|gaps?)|bank\s+funding)\b", re.IGNORECASE),
    re.compile(r"\bfinancial stability\b", re.IGNORECASE),
)

_INFLATION_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\binflation\b", re.IGNORECASE),
    re.compile(r"\bdisinflation\b", re.IGNORECASE),
    re.compile(r"\bhicp\b", re.IGNORECASE),
    re.compile(r"\bcore\s+(?:inflation|hicp)\b", re.IGNORECASE),
    re.compile(r"\bdeflation\b", re.IGNORECASE),
    re.compile(r"\bprice pressures\b", re.IGNORECASE),
    re.compile(r"\benergy prices\b", re.IGNORECASE),
    re.compile(r"\bfood prices\b", re.IGNORECASE),
)

_LABOUR_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\blabour market\b", re.IGNORECASE),
    re.compile(r"\blabor market\b", re.IGNORECASE),
    re.compile(r"\bunemployment\b", re.IGNORECASE),
    re.compile(r"\bemployment\b(?!\s+policy\b)", re.IGNORECASE),
    re.compile(r"\bwage(?:s)?\b(?!\s+policy\b)", re.IGNORECASE),
)

# Near-misses that must never be read as GDP growth: "GDP deflator" and
# "GDP per capita" (and the reversed "per capita GDP") are distinct measures
# and must never anchor (or emit) a GDP value fact on their own.
_GDP_NEAR_MISS = re.compile(
    r"(?:\bgdp\b\s+(?:deflator|per\s+capita)\b|per\s+capita\s+\bgdp\b)",
    re.IGNORECASE,
)
_GDP_ANCHOR = re.compile(
    r"(?<!per\scapita\s)\bgdp\b(?!\s+(?:deflator|per\s+capita)\b)",
    re.IGNORECASE,
)

_GROWTH_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bgrowth\b", re.IGNORECASE),
    _GDP_ANCHOR,
    re.compile(r"\b(?:economic\s+)?activity\b", re.IGNORECASE),
    re.compile(r"\beconom(?:y|ic)\b", re.IGNORECASE),
    re.compile(r"\b(?:real\s+output|industrial\s+output|output\s+growth|output\s+gaps?|potential\s+output)\b", re.IGNORECASE),
    re.compile(r"\bdemand\b", re.IGNORECASE),
    re.compile(r"\bconsumption\b", re.IGNORECASE),
    re.compile(r"\binvestment\b", re.IGNORECASE),
    re.compile(r"\bproduction\b", re.IGNORECASE),
    re.compile(r"\brecovery\b", re.IGNORECASE),
    re.compile(r"\brecession\b", re.IGNORECASE),
    re.compile(r"\bslowdown\b", re.IGNORECASE),
    re.compile(r"\bexpansion\b", re.IGNORECASE),
)

_FISCAL_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bfiscal\b", re.IGNORECASE),
    re.compile(r"\bdeficit\b", re.IGNORECASE),
    re.compile(r"\bsurplus\b", re.IGNORECASE),
    re.compile(r"\bgovernment debt\b", re.IGNORECASE),
    re.compile(r"\bpublic debt\b", re.IGNORECASE),
    re.compile(r"\bpublic finances\b", re.IGNORECASE),
    re.compile(r"\bfiscal position\b", re.IGNORECASE),
    re.compile(r"\bfiscal stance\b", re.IGNORECASE),
    re.compile(r"\b(?:fiscal\s+)?policy\s+(?:was|is|remains?)\s+(?:expected\s+to\s+be\s+)?neutral\b", re.IGNORECASE),
)

# ---------------------------------------------------------------------------
# Quantitative values — explicit value claims only. A percentage is only mined
# when the sentence states a value claim ("projected/expected/stood at …"). A
# share/ratio unit ("% of GDP") is never converted into a percentage fact, and
# a forecast value without an explicit reference period is ignored.
# ---------------------------------------------------------------------------
_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"
_MONTH_NUM = {
    "january": "01", "february": "02", "march": "03", "april": "04", "may": "05", "june": "06",
    "july": "07", "august": "08", "september": "09", "october": "10", "november": "11", "december": "12",
}
_QUARTER_NUM = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}
_VALUE_TOKEN = rf"{_RATE_ITEM}\s*(?:%|per\s+cent)"

_VALUE_GATE = re.compile(
    r"(?:projected|expected|forecast)\s+(?:to\s+)?(?:average|stand\s+at|be|reach|amount\s+to|"
    r"grow\s+by|grow\s+from|expand\s+by|expand\s+from|increase\s+(?:by|from|to)|rise\s+(?:by|from|to)|"
    r"decline\s+to|decline\s+from|fall\s+to|fall\s+from|remain\s+at)\s+"
    r"|(?:stood at|standing at|averaged|was at|were at|is at|are at|stands at|running at|remain(?:s|ed|ing)? at|"
    r"declined to|declined by|declined from|fell to|fell by|fell from|dropped to|dropped by|dropped from|"
    r"rose to|rose by|rose from|increased to|increased by|increased from|"
    r"expanded by|grew by|contracted by|narrowed to|widened to|reached)\s+",
    re.IGNORECASE,
)
_FORECAST_VERB = re.compile(r"\b(?:projected|expected|forecast)\b", re.IGNORECASE)
_PERCENT_WITH_QUARTER = re.compile(
    rf"(?P<token>{_VALUE_TOKEN})\s+(?P<period>in|during)\s+(?:the\s+)?(?P<quarter>first|second|third|fourth)\s+quarter\s+of\s+(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_PERCENT_WITH_MONTH = re.compile(
    rf"(?P<token>{_VALUE_TOKEN})\s+(?P<period>in|during)\s+(?P<month>{_MONTH_WORDS})\s+(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_PERCENT_WITH_YEAR = re.compile(
    rf"(?P<token>{_VALUE_TOKEN})\s+(?P<period>in|during|by|for)\s+(?:the\s+)?(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_VALUE_TOKEN_ONLY = re.compile(rf"(?P<token>{_VALUE_TOKEN})", re.IGNORECASE)
_SHARE_UNIT = re.compile(
    r"\bof\s+(?:gdp|gross\s+domestic\s+product|total|disposable\s+income)\b", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Tables — same value-gate philosophy as Phase 9: a cell becomes a Fact only
# when its table carries an explicit percentage unit, its row is a recognised
# economic variable and its columns are years. The unit is read from the
# table's own caption, never borrowed from another table.
# ---------------------------------------------------------------------------
_YEAR_CELL = re.compile(r"^\s*(20[0-9]{2})\s*$")
_NO_DATA = {"", "-", "–", "—", "…", "..", ".", "n.a.", "n/a", "na", "nd"}

_TBL_UNIT_SHARE = (
    r"%\s*of\s*gdp",
    r"percentage\s*of\s*gdp",
    r"%\s*of\s*gross\s*domestic\s*product",
    r"%\s*of\s*total",
    r"%\s*of\s*disposable\s*income",
)
_TBL_UNIT_PERCENTAGE = (
    r"annual\s+percentage\s+changes?",
    r"percentage\s+changes?",
    r"annual\s+growth\s+rates?",
    r"\bper\s?cent\b",
    r"\bpercent\b",
    r"%\s*changes?",
    r"%\s*growth",
    r"\(\s*%\s*\)",
)
_TBL_UNIT_INCOMPATIBLE = (
    r"\bindex\b",
    r"\bpoints\b",
    r"\busd\b",
    r"\beur\b",
    r"\beuro\b",
    r"\bmwh\b",
    r"\bkwh\b",
    r"\btonnes?\b",
    r"\bbarrel\b",
)

_TBL_CORE = frozenset({"core inflation", "core hicp", "hicp excluding energy and food", "hicpx"})
_TBL_INFLATION = frozenset({"inflation", "hicp", "hicp inflation", "hicp inflation rate", "headline inflation"})
_TBL_GDP = frozenset({"real gdp", "gdp", "gdp growth", "real gdp growth"})
_TBL_UNEMPLOYMENT = frozenset({"unemployment", "unemployment rate"})
_TBL_EMPLOYMENT = frozenset({"employment", "total employment"})
_TBL_WAGES = frozenset({"wages", "wage growth", "compensation per employee", "negotiated wages"})

_PARENTHESIS = re.compile(r"\([^)]*\)")


def _clean_row_label(cell: str) -> str:
    """Normalise a table row label: strip footnote markers and parenthetical
    qualifiers, collapse whitespace, lowercase. ``""`` when empty."""
    raw = normalize_title(cell or "")
    raw = _FOOTNOTE_MARK.sub("", raw).strip()
    raw = _PARENTHESIS.sub("", raw).strip()
    return " ".join(raw.split())


def _table_subject(cell: str) -> str | None:
    t = _clean_row_label(cell)
    if not t:
        return None
    if t in _TBL_CORE:
        return SUBJECT_CORE_INFLATION
    if t in _TBL_INFLATION:
        return SUBJECT_INFLATION
    if t in _TBL_GDP:
        return SUBJECT_GDP
    if t in _TBL_UNEMPLOYMENT:
        return SUBJECT_UNEMPLOYMENT
    if t in _TBL_EMPLOYMENT:
        return SUBJECT_LABOUR_MARKET
    if t in _TBL_WAGES:
        return SUBJECT_WAGES
    return None


def _table_unit(name: str) -> str | None:
    """Return ``"percentage"`` when the table caption explicitly states a
    percentage unit, else ``None`` (missing, unknown or incompatible)."""
    t = (name or "").strip().lower()
    if not t:
        return None
    if any(re.search(marker, t) for marker in _TBL_UNIT_SHARE):
        return None
    if any(re.search(marker, t) for marker in _TBL_UNIT_PERCENTAGE):
        return "percentage"
    if any(re.search(marker, t) for marker in _TBL_UNIT_INCOMPATIBLE):
        return None
    return None


def _numeric(cell: str) -> float | None:
    raw = (cell or "").strip()
    if not raw:
        return None
    cleaned = _FOOTNOTE_MARK.sub("", raw).strip()
    if cleaned.lower() in _NO_DATA:
        return None
    cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _split_sentences(text: str) -> list[str]:
    """Split paragraph text into non-empty sentences, each verbatim (trailing
    period preserved). Both ``". "`` and ``".\\n"`` are boundaries."""
    return [part.strip() for part in re.split(r"(?<=\.)\s+", text or "") if part.strip()]


class _RunState:
    """Mutable run state threaded through the miners."""

    __slots__ = ("risk_found", "guidance_found", "economic_processed")

    def __init__(self) -> None:
        self.risk_found = False
        self.guidance_found = False
        self.economic_processed = False


class EcbReportsExtractor(ReportsExtractor):
    bank = "ecb"
    extraction_version = EXTRACTION_VERSION

    def extract(self, publication, document: NormalizedDocument) -> ExtractionResult:
        result = ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )
        if not document.sections and not document.tables:
            result.warnings.append("no_sections")
            return result

        counters: dict[tuple, int] = {}
        seen: set[tuple] = set()
        state = _RunState()

        for index, section in enumerate(document.sections):
            if _section_category(section.heading or "") in (CAT_IGNORE, CAT_UNKNOWN):
                continue
            state.economic_processed = True
            self._process_section(result, document, index, section.text or "", counters, seen, state)

        for tindex, table in enumerate(document.tables):
            self._process_table(result, document, tindex, table, counters, seen, state)

        if not state.economic_processed:
            result.warnings.append("no_economic_sections")
        if not state.risk_found:
            result.warnings.append("no_risk_assessment")
        if not state.guidance_found:
            result.warnings.append("no_forward_guidance")
        return result

    # ------------------------------------------------------------------
    # section walking — every recognized section is mined content-first
    # ------------------------------------------------------------------
    def _process_section(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        text: str,
        counters: dict,
        seen: set,
        state: _RunState,
    ) -> None:
        for sentence in _split_sentences(text):
            self._mine_sentence(result, document, index, sentence, counters, seen, state)

    # ------------------------------------------------------------------
    # sentence classification (guidance > policy > risk > financial >
    # inflation > labour > growth > fiscal) and fact emission
    # ------------------------------------------------------------------
    def _mine_sentence(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        counters: dict,
        seen: set,
        state: _RunState,
    ) -> None:
        category = self._categorize(sentence)
        if category == CAT_GUIDANCE:
            state.guidance_found = True
            self._emit_text(result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, PREDICATE_STATEMENT, counters, seen)
        elif category == CAT_POLICY:
            self._emit_text(result, document, index, sentence, SUBJECT_MONETARY_POLICY, PREDICATE_STATEMENT, counters, seen)
        elif category == CAT_RISK:
            state.risk_found = True
            self._add_risk_facts(result, document, index, sentence, counters, seen)
        elif category == CAT_FINANCIAL:
            if not self._add_value_facts(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, counters, seen):
                self._emit_text(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, PREDICATE_ASSESSMENT, counters, seen)
        elif category == CAT_INFLATION:
            self._add_inflation_facts(result, document, index, sentence, counters, seen)
        elif category == CAT_LABOUR:
            self._add_labour_facts(result, document, index, sentence, counters, seen)
        elif category == CAT_GROWTH:
            self._add_growth_facts(result, document, index, sentence, counters, seen)
        elif category == CAT_FISCAL:
            self._emit_text(result, document, index, sentence, SUBJECT_FISCAL_POLICY, PREDICATE_ASSESSMENT, counters, seen)

    def _categorize(self, sentence: str) -> str:
        if self._matches(_GUIDANCE_ANCHORS, sentence):
            return CAT_GUIDANCE
        if _is_policy_sentence(sentence):
            return CAT_POLICY
        if self._matches(_RISK_ANCHORS, sentence):
            return CAT_RISK
        if self._matches(_FINANCIAL_ANCHORS, sentence):
            return CAT_FINANCIAL
        if self._matches(_INFLATION_ANCHORS, sentence):
            return CAT_INFLATION
        if self._matches(_LABOUR_ANCHORS, sentence):
            return CAT_LABOUR
        if self._matches(_GROWTH_ANCHORS, sentence):
            return CAT_GROWTH
        if self._matches(_FISCAL_ANCHORS, sentence):
            return CAT_FISCAL
        return CAT_NONE

    @staticmethod
    def _matches(anchors: tuple[re.Pattern, ...], sentence: str) -> bool:
        return any(anchor.search(sentence) for anchor in anchors)

    # ------------------------------------------------------------------
    # emission with within-run deduplication
    # ------------------------------------------------------------------
    @classmethod
    def _emit(
        cls,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        source_text: str,
        subject: str,
        predicate: str,
        value: FactValue,
        confidence: Confidence,
        counters: dict,
        seen: set,
        *,
        period: FactPeriod | None = None,
        method: str = METHOD_REGEX,
        location: FactLocation | None = None,
    ) -> Fact | None:
        """Build a Fact with a deterministic ordinal qualifier, suppressing
        within-run duplicates. A quantitative duplicate is defined by
        subject + predicate + period + value; a qualitative one by subject +
        predicate + period + normalized verbatim wording."""
        period_key = period.canonical() if period else ""
        if value.kind in (ValueKind.NUMBER, ValueKind.PERCENTAGE, ValueKind.BASIS_POINTS, ValueKind.CURRENCY):
            dedup_key = (subject, predicate, period_key, value.value)
        else:
            dedup_key = (subject, predicate, period_key, normalize_title(source_text or ""))
        if dedup_key in seen:
            return None
        seen.add(dedup_key)

        key = (subject, predicate, period_key)
        ordinal = counters.get(key, 0)
        counters[key] = ordinal + 1

        loc = location if location is not None else FactLocation(LocationKind.SECTION, section=index)
        return Fact(
            publication_id=result.publication_id,
            document_id=document.document_id,
            subject=subject,
            predicate=predicate,
            value=value,
            period=period,
            effective_date=None,
            source_location=loc,
            source_text=source_text,
            extraction_method=method,
            extraction_version=EXTRACTION_VERSION,
            confidence=confidence,
            speaker=None,
            identity_qualifier=f"report:{subject}:{ordinal}",
        )

    @classmethod
    def _emit_text(
        cls,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        subject: str,
        predicate: str,
        counters: dict,
        seen: set,
    ) -> None:
        fact = cls._emit(
            result, document, index, sentence, subject, predicate,
            FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
            Confidence.MEDIUM, counters, seen,
        )
        if fact is not None:
            result.add(fact)

    # ------------------------------------------------------------------
    # risk assessment
    # ------------------------------------------------------------------
    @classmethod
    def _add_risk_facts(cls, result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, counters: dict, seen: set) -> None:
        """A risk sentence yields a categorical orientation fact when an
        explicit orientation word is present; otherwise a verbatim text
        assessment. The risk target is read from the sentence wording."""
        lower = sentence.lower()
        if "inflation" in lower:
            subject = SUBJECT_INFLATION_RISK
        elif "growth" in lower or "activity" in lower or "gdp" in lower:
            subject = SUBJECT_GROWTH_RISK
        else:
            subject = SUBJECT_RISK

        orientation = None
        if re.search(r"\b(?:broadly\s+)?balanced\b|\btwo-sided\b|\bsymmetric\b", lower):
            orientation = "balanced"
        elif re.search(r"\bdownside\b", lower):
            orientation = "downside"
        elif re.search(r"\bupside\b", lower):
            orientation = "upside"

        if orientation is not None:
            fact = cls._emit(
                result, document, index, sentence, subject, PREDICATE_ASSESSMENT,
                categorical(orientation, source_text=sentence),
                Confidence.HIGH, counters, seen,
            )
        else:
            fact = cls._emit(
                result, document, index, sentence, subject, PREDICATE_ASSESSMENT,
                FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
                Confidence.MEDIUM, counters, seen,
            )
        if fact is not None:
            result.add(fact)

    # ------------------------------------------------------------------
    # quantitative value claims
    # ------------------------------------------------------------------
    @classmethod
    def _add_value_facts(cls, result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, subject: str, counters: dict, seen: set) -> int:
        """Emit ``subject/value`` percentage facts for explicit value claims.

        A percentage followed by an explicit reference period (year, month or
        quarter) keeps it as ``FactPeriod``; a percentage with no stated period
        is kept without one — except for *forecasts*, which are under-determined
        without a reference period and are therefore ignored. Share/ratio units
        ("% of GDP") are never converted into percentage facts. Returns the
        number of facts emitted.
        """
        if not _VALUE_GATE.search(sentence):
            return 0

        forecast = bool(_FORECAST_VERB.search(sentence))
        covered: list[tuple[int, int]] = []
        emitted = 0
        claimed = False  # a genuine value claim was present (even if deduped or ignored)

        for match in _PERCENT_WITH_QUARTER.finditer(sentence):
            token = match.group("token")
            if _is_share(sentence, match):
                continue
            claimed = True
            period = FactPeriod(
                PeriodKind.QUARTER,
                f"{match.group('year')}-{_QUARTER_NUM[match.group('quarter').lower()]}",
                label=sentence[match.start("period") : match.end()],
            )
            covered.append((match.start("token"), match.end("token")))
            fact = cls._emit(
                result, document, index, sentence, subject, PREDICATE_VALUE,
                percentage(cls._token_value(token), source_text=token),
                Confidence.HIGH, counters, seen, period=period,
            )
            if fact is not None:
                result.add(fact)
                emitted += 1

        for match in _PERCENT_WITH_MONTH.finditer(sentence):
            token = match.group("token")
            if _is_share(sentence, match):
                continue
            claimed = True
            period = FactPeriod(
                PeriodKind.MONTH,
                f"{match.group('year')}-{_MONTH_NUM[match.group('month').lower()]}",
                label=sentence[match.start("period") : match.end()],
            )
            covered.append((match.start("token"), match.end("token")))
            fact = cls._emit(
                result, document, index, sentence, subject, PREDICATE_VALUE,
                percentage(cls._token_value(token), source_text=token),
                Confidence.HIGH, counters, seen, period=period,
            )
            if fact is not None:
                result.add(fact)
                emitted += 1

        for match in _PERCENT_WITH_YEAR.finditer(sentence):
            token = match.group("token")
            if _is_share(sentence, match):
                continue
            claimed = True
            period = FactPeriod(
                PeriodKind.YEAR,
                match.group("year"),
                label=sentence[match.start("period") : match.end()],
            )
            covered.append((match.start("token"), match.end("token")))
            fact = cls._emit(
                result, document, index, sentence, subject, PREDICATE_VALUE,
                percentage(cls._token_value(token), source_text=token),
                Confidence.HIGH, counters, seen, period=period,
            )
            if fact is not None:
                result.add(fact)
                emitted += 1

        for match in _VALUE_TOKEN_ONLY.finditer(sentence):
            if any(start <= match.start("token") and match.end("token") <= end for start, end in covered):
                continue
            if _is_share(sentence, match):
                continue
            claimed = True
            if forecast:
                continue  # a forecast without an explicit reference period is ignored
            token = match.group("token")
            fact = cls._emit(
                result, document, index, sentence, subject, PREDICATE_VALUE,
                percentage(cls._token_value(token), source_text=token),
                Confidence.HIGH, counters, seen,
            )
            if fact is not None:
                result.add(fact)
                emitted += 1

        return emitted if emitted else claimed

    @staticmethod
    def _token_value(token: str) -> float:
        return float(re.match(r"[0-9.]+", token).group(0))

    # ------------------------------------------------------------------
    # tables — variable × period × value × unit (Phase 9 philosophy)
    # ------------------------------------------------------------------
    @classmethod
    def _process_table(
        cls,
        result: ExtractionResult,
        document: NormalizedDocument,
        tindex: int,
        table,
        counters: dict,
        seen: set,
        state: _RunState,
    ) -> None:
        if _table_unit(table.name) != "percentage":
            return  # missing / unknown / incompatible unit → whole table ignored

        year_cols: list[tuple[int, str]] = []
        for cidx, cell in enumerate(table.headers):
            match = _YEAR_CELL.match(cell or "")
            if match is not None:
                year_cols.append((cidx, match.group(1)))
        if not year_cols:
            return  # no year columns → not an economic data table

        for rindex, row in enumerate(table.rows):
            if not row:
                continue
            subject = _table_subject(row[0])
            if subject is None:
                continue  # unrecognised variable row → ignored
            row_text = " | ".join(str(cell or "") for cell in row)
            for cidx, year in year_cols:
                cell = row[cidx] if cidx < len(row) else ""
                value = _numeric(cell)
                if value is None:
                    continue
                period = FactPeriod(PeriodKind.YEAR, year, label=(table.headers[cidx] or "").strip())
                fact = cls._emit(
                    result, document, -1, row_text, subject, PREDICATE_VALUE,
                    percentage(value, source_text=(cell or "").strip()),
                    Confidence.HIGH, counters, seen,
                    period=period,
                    method=METHOD_TABLE,
                    location=FactLocation(LocationKind.TABLE, table=tindex, row=rindex, column=cidx),
                )
                if fact is not None:
                    result.add(fact)
                    state.economic_processed = True

    # ------------------------------------------------------------------
    # inflation / growth / labour market
    # ------------------------------------------------------------------
    @classmethod
    def _add_inflation_facts(cls, result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, counters: dict, seen: set) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        elif "core inflation" in lower or "core hicp" in lower:
            subject = SUBJECT_CORE_INFLATION
        else:
            subject = SUBJECT_INFLATION
        if cls._add_value_facts(result, document, index, sentence, subject, counters, seen):
            return
        cls._emit_text(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters, seen)

    @classmethod
    def _add_growth_facts(cls, result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, counters: dict, seen: set) -> None:
        # A GDP deflator / per-capita mention inside an otherwise-growth
        # sentence must not leak into a GDP value fact (precision first).
        if _GDP_NEAR_MISS.search(sentence):
            return
        if cls._add_value_facts(result, document, index, sentence, SUBJECT_GDP, counters, seen):
            return
        cls._emit_text(result, document, index, sentence, SUBJECT_GROWTH, PREDICATE_ASSESSMENT, counters, seen)

    @classmethod
    def _add_labour_facts(cls, result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, counters: dict, seen: set) -> None:
        lower = sentence.lower()
        if "unemployment" in lower:
            subject = SUBJECT_UNEMPLOYMENT
        elif "wage" in lower:
            subject = SUBJECT_WAGES
        else:
            subject = SUBJECT_LABOUR_MARKET
        if cls._add_value_facts(result, document, index, sentence, subject, counters, seen):
            return
        cls._emit_text(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters, seen)


def _is_share(sentence: str, match: re.Match) -> bool:
    """True when the value token is followed by a share/ratio unit ("% of
    GDP", "% of total", …) — such ratios are never stored as percentages."""
    window = sentence[match.end("token") : match.end("token") + 80]
    return bool(_SHARE_UNIT.search(window))
