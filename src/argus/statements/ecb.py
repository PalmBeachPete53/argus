"""ECB — Monetary Policy Statement extractor (Phase 6).

Extracts the facts of an ECB Monetary Policy Statement from the normalized
document text, answering "what does the Governing Council explicitly state about
the economy and its policy stance in the statement?":

- the decision rationale — sentences that justify the decision ("are based on",
  "in order to", "to ensure that", "consistent with", …), verbatim
- forward guidance — explicit prospective policy statements ("stands ready to
  adjust", "for as long as necessary", "will be guided by", …), verbatim
- the macro-economic assessment:
  - inflation (``inflation`` / ``core_inflation`` / ``inflation_expectations``)
  - growth (``growth`` qualitative, ``gdp`` quantitative)
  - labour market (``labour_market`` / ``unemployment`` / ``wages``)
  - financial conditions
  - the risk assessment, as categorical orientations (``upside`` / ``downside``
    / ``balanced``) or verbatim text when no orientation is stated
- explicit quantitative values ("projected to average 2.2% in 2027") with the
  verbatim reference period (year / month), and never without source wording.

Deliberately NOT extracted (Phase 6 boundary):

- the decision itself (wording/rates) — that is Phase 5 territory, gated on
  decision publications
- votes, hawkish/dovish interpretation, forex fundamentals, formulation-change
  analysis (Phase 12) — none of these is ever invented here

Design rules

- No fact is invented. A value/orientation is only produced when the source
  states it, and every Fact preserves an *exact verbatim* supporting passage
  (``source_text``) copied from the normalized document.
- Content is routed deterministically by section heading (risk / inflation /
  growth / labour market / financial conditions / forward guidance), with a
  narrow content-first fallback (guidance > risk > rationale) for sections
  whose heading carries no signal (intro, closing remarks, heading-less text).
- Quantitative facts carry ``FactPeriod`` only when the source states an
  explicit reference period; a bare percentage is kept with no period. The
  "2% target" phrasing is never mined as a value — only sentences with an
  explicit value claim ("projected/expected/stood at …") are.
- Confidence is ``HIGH`` for quantitative percentages and categorical risk
  orientations (explicit source wording); ``MEDIUM`` for verbatim qualitative
  assessments (sentence-level category identification).
- Absence of an optional section (no risk assessment, no forward guidance)
  never becomes an invented "balanced" / "no guidance" fact; it is surfaced as
  a warning instead.
- Guidance is preserved verbatim and never interpreted as an expectation or
  stance.
"""

from __future__ import annotations

import re

from ..classification.base import Confidence
from ..documents.base import NormalizedDocument
from ..facts import (
    METHOD_REGEX,
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
from .base import StatementExtractor

EXTRACTION_VERSION = "6.0.0"

# ---------------------------------------------------------------------------
# Canonical Phase 6 subjects (controlled vocabulary, see docs/EXTRACTORS.md).
# ``policy_guidance`` is shared with Phase 5: statement-level guidance is the
# same content type, only the publication type differs.
# ---------------------------------------------------------------------------
SUBJECT_MONETARY_POLICY = "monetary_policy"
SUBJECT_INFLATION = "inflation"
SUBJECT_CORE_INFLATION = "core_inflation"
SUBJECT_INFLATION_EXPECTATIONS = "inflation_expectations"
SUBJECT_GROWTH = "growth"
SUBJECT_GDP = "gdp"
SUBJECT_LABOUR_MARKET = "labour_market"
SUBJECT_UNEMPLOYMENT = "unemployment"
SUBJECT_WAGES = "wages"
SUBJECT_FINANCIAL_CONDITIONS = "financial_conditions"
SUBJECT_INFLATION_RISK = "inflation_risk"
SUBJECT_GROWTH_RISK = "growth_risk"
SUBJECT_RISK = "risk"
SUBJECT_POLICY_GUIDANCE = "policy_guidance"

PREDICATE_ASSESSMENT = "assessment"
PREDICATE_RATIONALE = "rationale"
PREDICATE_STATEMENT = "statement"
PREDICATE_VALUE = "value"

_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"
_MONTH_NUM = {
    "january": "01", "february": "02", "march": "03", "april": "04", "may": "05", "june": "06",
    "july": "07", "august": "08", "september": "09", "october": "10", "november": "11", "december": "12",
}
# A single percentage value token, verbatim, e.g. "2.00%" or "1.75 per cent".
_VALUE_TOKEN = rf"{_RATE_ITEM}\s*(?:%|per\s+cent)"

# ---------------------------------------------------------------------------
# Section routing: normalized heading → category. Routing is deterministic and
# precedes any content scanning, so cross-category phrasing inside a mapped
# section is never double-counted.
# ---------------------------------------------------------------------------
CAT_INTRO = "intro"
CAT_FINANCIAL = "financial_conditions"
CAT_LABOUR = "labour_market"
CAT_RISK = "risk"
CAT_INFLATION = "inflation"
CAT_GROWTH = "growth"
CAT_GUIDANCE = "guidance"
CAT_UNCLASSIFIED = "unclassified"


_INTRO_HEADINGS = frozenset({
    "monetary policy statement",
    "monetary policy decisions",
    "monetary policy",
    "policy",
    "policy stance",
    "monetary policy stance",
    "policy considerations",
    "policy conclusions",
})
_RISK_HEADINGS = frozenset({"risk assessment", "risks", "risk"})
_GUIDANCE_HEADINGS = frozenset({"forward guidance", "guidance"})
_INFLATION_HEADINGS = frozenset({"inflation", "prices and costs", "price developments", "inflation outlook"})
_GROWTH_HEADINGS = frozenset({
    "economic activity",
    "real economy",
    "growth",
    "economic outlook",
    "euro area economy",
    "the euro area economy",
    "economic developments",
})
_LABOUR_HEADINGS = frozenset({
    "labour market",
    "labour market developments",
    "employment",
    "unemployment",
})
_FINANCIAL_HEADINGS = frozenset({
    "financial conditions",
    "financial market conditions",
    "financing conditions",
    "monetary and financial conditions",
    "money, credit and financial conditions",
})

_LEADING_NUM = re.compile(r"^\s*(?:[0-9]+(?:\.[0-9]+)*)\s*[-–—.:]?\s*")
_FOOTNOTE_MARK = re.compile(r"\s*(?:\(\d+\)|\[\d+\]|\d+\)|[*†‡]+)\s*$")
_LEADING_THE = re.compile(r"^the\s+")
_TRAILING_PUNCT = re.compile(r"[\s.:;,\-–—]+$")


def _clean_heading(heading: str) -> str:
    t = normalize_title(heading or "")
    if not t:
        return ""
    t = _LEADING_NUM.sub("", t).strip()
    t = _FOOTNOTE_MARK.sub("", t).strip()
    t = _LEADING_THE.sub("", t).strip()
    return _TRAILING_PUNCT.sub("", t).strip()


def _section_category(heading: str) -> str:
    """Route a section by its normalized heading. Routing is exact identity on
    the cleaned heading: a marker inside a near-miss heading ("Risk management"
    contains "risk", "Non-economic developments" contains "economic") never
    routes to a known economic section — it falls through to the narrow
    content-first fallback (CAT_UNCLASSIFIED)."""
    t = _clean_heading(heading)
    if not t:
        return CAT_UNCLASSIFIED
    if t in _INTRO_HEADINGS:
        return CAT_INTRO
    if t in _FINANCIAL_HEADINGS:
        return CAT_FINANCIAL
    if t in _LABOUR_HEADINGS:
        return CAT_LABOUR
    if t in _RISK_HEADINGS:
        return CAT_RISK
    if t in _INFLATION_HEADINGS:
        return CAT_INFLATION
    if t in _GROWTH_HEADINGS:
        return CAT_GROWTH
    if t in _GUIDANCE_HEADINGS:
        return CAT_GUIDANCE
    return CAT_UNCLASSIFIED


# ---------------------------------------------------------------------------
# Anchors. Forward guidance is checked before rationale, so a guidance sentence
# that happens to justify itself ("… to ensure that …") stays a guidance fact.
# ---------------------------------------------------------------------------
_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bstands?\s+ready\s+to\s+adjust\b", re.IGNORECASE),
    re.compile(r"\bwill\s+not\s+hesitate\s+to\s+adjust\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bdoes\s+not\s+pre-?commit\b", re.IGNORECASE),
    re.compile(r"\bexpects?\s+(?:the\s+)?(?:key\s+)?(?:ecb\s+)?interest\s+rates?\s+to\s+remain\b", re.IGNORECASE),
    re.compile(r"\bwill\s+keep\s+(?:the\s+)?(?:key\s+)?(?:ecb\s+)?interest\s+rates?\b", re.IGNORECASE),
    re.compile(r"\b(?:maintain|maintaining)\s+(?:an?\s+|the\s+)?(?:accommodative|restrictive)\b", re.IGNORECASE),
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess|follow)\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
    re.compile(r"\bwill\s+depend\s+on\b", re.IGNORECASE),
    re.compile(r"\bdata-dependent\b", re.IGNORECASE),
)

_RISK_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\brisks?\s+(?:to|for|around|surrounding|from|of|are|were|remain|remained|have|has)\b", re.IGNORECASE),
    re.compile(r"\b(?:downside|upside|two-sided|symmetric|broadly\s+balanced)\s+risks?\b", re.IGNORECASE),
    re.compile(r"\buncertain(?:ty|ties)?\b", re.IGNORECASE),
    re.compile(r"\btilted\b", re.IGNORECASE),
)

_RATIONALE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(?:in order to|so as to)\b", re.IGNORECASE),
    re.compile(r"\bto ensure that\b", re.IGNORECASE),
    re.compile(r"\bwill contribute to\b", re.IGNORECASE),
    re.compile(r"\b(?:is|are)\s+based\s+on\b", re.IGNORECASE),
    re.compile(r"\bin light of\b", re.IGNORECASE),
    re.compile(r"\bconsistent with\b", re.IGNORECASE),
    re.compile(r"\baimed at\b", re.IGNORECASE),
    re.compile(r"\bdesigned to\b", re.IGNORECASE),
    re.compile(r"\bseeks? to\b", re.IGNORECASE),
    re.compile(r"\bwith a view to\b", re.IGNORECASE),
    re.compile(r"\bto safeguard\b", re.IGNORECASE),
)

# A sentence is mined for quantitative values only when it states an explicit
# value claim — "projected/expected … to average/stand at/be", "stood at", … —
# so "the 2% target" or "converging towards 2%" is never read as a value.
_VALUE_GATE = re.compile(
    r"(?:projected|expected|forecast)\s+(?:to\s+)?(?:average|stand\s+at|be|reach|amount\s+to|grow\s+by|expand\s+by)\s+"
    r"|(?:stood at|averaged|was at|were at|is at|are at|stands at|running at|remain(?:s|ed)? at|declined to|fell to|"
    r"dropped to|rose to|increased to|reached)\s+",
    re.IGNORECASE,
)

# Percentage token immediately followed by a reference period (verbatim).
_PERCENT_WITH_YEAR = re.compile(
    rf"(?P<token>{_VALUE_TOKEN})\s+(?P<period>in|during|by|for)\s+(?:the\s+)?(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_PERCENT_WITH_MONTH = re.compile(
    rf"(?P<token>{_VALUE_TOKEN})\s+(?P<period>in|during)\s+(?P<month>{_MONTH_WORDS})\s+(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_VALUE_TOKEN_ONLY = re.compile(rf"(?P<token>{_VALUE_TOKEN})", re.IGNORECASE)


def _split_sentences(text: str) -> list[str]:
    """Split normalized section text into non-empty sentences, each verbatim
    (trailing period preserved). Both ``". "`` and ``".\\n"`` are boundaries."""
    return [part.strip() for part in re.split(r"(?<=\.)\s+", text or "") if part.strip()]


class EcbMonetaryPolicyStatementExtractor(StatementExtractor):
    bank = "ecb"
    extraction_version = EXTRACTION_VERSION

    def extract(self, publication, document: NormalizedDocument) -> ExtractionResult:
        result = ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )
        if not document.sections:
            result.warnings.append("no_sections")
            return result

        counters: dict[str, int] = {}
        risk_found = False
        guidance_found = False

        for index, section in enumerate(document.sections):
            category = _section_category(section.heading or "")
            for sentence in _split_sentences(section.text or ""):
                if category in (CAT_INTRO, CAT_UNCLASSIFIED):
                    if self._matches(_GUIDANCE_ANCHORS, sentence):
                        guidance_found = True
                        result.add(
                            self._text_fact(result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, PREDICATE_STATEMENT, counters)
                        )
                    elif self._matches(_RISK_ANCHORS, sentence):
                        risk_found = True
                        self._add_risk_facts(result, document, index, sentence, counters)
                    elif self._matches(_RATIONALE_ANCHORS, sentence):
                        result.add(
                            self._text_fact(result, document, index, sentence, SUBJECT_MONETARY_POLICY, PREDICATE_RATIONALE, counters)
                        )
                elif category == CAT_GUIDANCE:
                    guidance_found = True
                    result.add(
                        self._text_fact(result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, PREDICATE_STATEMENT, counters)
                    )
                elif category == CAT_RISK:
                    risk_found = True
                    self._add_risk_facts(result, document, index, sentence, counters)
                elif category == CAT_FINANCIAL:
                    result.add(
                        self._text_fact(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, PREDICATE_ASSESSMENT, counters)
                    )
                elif category == CAT_INFLATION:
                    self._add_inflation_facts(result, document, index, sentence, counters)
                elif category == CAT_GROWTH:
                    self._add_growth_facts(result, document, index, sentence, counters)
                elif category == CAT_LABOUR:
                    self._add_labour_facts(result, document, index, sentence, counters)

        if not risk_found:
            result.warnings.append("no_risk_assessment")
        if not guidance_found:
            result.warnings.append("no_forward_guidance")
        return result

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _matches(anchors: tuple[re.Pattern, ...], sentence: str) -> bool:
        return any(anchor.search(sentence) for anchor in anchors)

    @classmethod
    def _text_fact(cls, result, document, index: int, sentence: str, subject: str, predicate: str, counters: dict) -> Fact:
        return cls._fact(
            result, document, index, sentence, subject, predicate,
            FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
            Confidence.MEDIUM, counters,
        )

    @staticmethod
    def _fact(result, document, index: int, sentence: str, subject: str, predicate: str, value, confidence, counters: dict, *, period=None) -> Fact:
        ordinal = counters.get(subject, 0)
        counters[subject] = ordinal + 1
        return Fact(
            publication_id=result.publication_id,
            document_id=document.document_id,
            subject=subject,
            predicate=predicate,
            value=value,
            period=period,
            effective_date=None,
            source_location=FactLocation(LocationKind.SECTION, section=index),
            source_text=sentence,
            extraction_method=METHOD_REGEX,
            extraction_version=EXTRACTION_VERSION,
            confidence=confidence,
            identity_qualifier=f"{subject}:{ordinal}",
        )

    # ------------------------------------------------------------------
    # risk assessment
    # ------------------------------------------------------------------
    @classmethod
    def _add_risk_facts(cls, result, document, index: int, sentence: str, counters: dict) -> None:
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
            result.add(
                cls._fact(
                    result, document, index, sentence, subject, PREDICATE_ASSESSMENT,
                    categorical(orientation, source_text=sentence),
                    Confidence.HIGH, counters,
                )
            )
        else:
            result.add(cls._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters))

    # ------------------------------------------------------------------
    # quantitative value claims (shared by inflation / growth / labour)
    # ------------------------------------------------------------------
    @classmethod
    def _add_value_facts(cls, result, document, index: int, sentence: str, subject: str, counters: dict) -> int:
        """Emit ``subject/value`` percentage facts for explicit value claims.

        A percentage followed by a reference period keeps it as ``FactPeriod``
        (year, or month when the source names a month); a percentage with no
        period is kept without one. Returns the number of facts emitted (0 when
        the sentence is not a value claim).
        """
        if not _VALUE_GATE.search(sentence):
            return 0

        covered: list[tuple[int, int]] = []
        emitted = 0

        for match in _PERCENT_WITH_MONTH.finditer(sentence):
            token = match.group("token")
            period = FactPeriod(
                PeriodKind.MONTH,
                f"{match.group('year')}-{_MONTH_NUM[match.group('month').lower()]}",
                label=sentence[match.start("period") : match.end()],
            )
            covered.append((match.start("token"), match.end("token")))
            result.add(
                cls._fact(
                    result, document, index, sentence, subject, PREDICATE_VALUE,
                    percentage(cls._token_value(token), source_text=token),
                    Confidence.HIGH, counters,
                    period=period,
                )
            )
            emitted += 1

        for match in _PERCENT_WITH_YEAR.finditer(sentence):
            token = match.group("token")
            period = FactPeriod(
                PeriodKind.YEAR,
                match.group("year"),
                label=sentence[match.start("period") : match.end()],
            )
            covered.append((match.start("token"), match.end("token")))
            result.add(
                cls._fact(
                    result, document, index, sentence, subject, PREDICATE_VALUE,
                    percentage(cls._token_value(token), source_text=token),
                    Confidence.HIGH, counters,
                    period=period,
                )
            )
            emitted += 1

        for match in _VALUE_TOKEN_ONLY.finditer(sentence):
            if any(start <= match.start("token") and match.end("token") <= end for start, end in covered):
                continue
            token = match.group("token")
            result.add(
                cls._fact(
                    result, document, index, sentence, subject, PREDICATE_VALUE,
                    percentage(cls._token_value(token), source_text=token),
                    Confidence.HIGH, counters,
                )
            )
            emitted += 1

        return emitted

    @staticmethod
    def _token_value(token: str) -> float:
        return float(re.match(r"[0-9.]+", token).group(0))

    # ------------------------------------------------------------------
    # inflation / growth / labour market
    # ------------------------------------------------------------------
    @classmethod
    def _add_inflation_facts(cls, result, document, index: int, sentence: str, counters: dict) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        elif "core inflation" in lower or "core hicp" in lower:
            subject = SUBJECT_CORE_INFLATION
        else:
            subject = SUBJECT_INFLATION
        if cls._add_value_facts(result, document, index, sentence, subject, counters):
            return
        result.add(cls._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters))

    @classmethod
    def _add_growth_facts(cls, result, document, index: int, sentence: str, counters: dict) -> None:
        if cls._add_value_facts(result, document, index, sentence, SUBJECT_GDP, counters):
            return
        result.add(cls._text_fact(result, document, index, sentence, SUBJECT_GROWTH, PREDICATE_ASSESSMENT, counters))

    @classmethod
    def _add_labour_facts(cls, result, document, index: int, sentence: str, counters: dict) -> None:
        lower = sentence.lower()
        if "unemployment" in lower:
            subject = SUBJECT_UNEMPLOYMENT
        elif "wage" in lower:
            subject = SUBJECT_WAGES
        else:
            subject = SUBJECT_LABOUR_MARKET
        if cls._add_value_facts(result, document, index, sentence, subject, counters):
            return
        result.add(cls._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters))