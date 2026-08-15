"""Structural helpers shared by the bank-specific Report extractors (Phase 4.x).

These helpers are deliberately **structural only**: normalized-heading cleaning,
sentence splitting, value-token parsing, the explicit value-claim gate and a
deterministic, provenance-carrying fact emitter with within-run deduplication.

They carry **no bank-specific semantics**: every bank's heading vocabulary,
expected section structure and subject routing live in that bank's own module
(`fixme-yes-bank-specific`). Nothing here knows about any particular central
bank's report layout or vocabulary. Reusing these helpers across banks is the
"genuinely structural operations" the phase contract allows; encoding bank
wording here is forbidden.
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

# Canonical Phase 4.x Report subjects (controlled vocabulary).
SUBJECT_INFLATION = "inflation"
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
# heading normalization (structural) — case, numbering, footnotes, leading
# "the", trailing punctuation, collapsed whitespace. Nothing else.
# ---------------------------------------------------------------------------
_FOOTNOTE_MARK = re.compile(r"\s*(?:\(\d+\)|\[\d+\]|\d+\)|[*†‡]+)\s*$")
_BOX_PREFIX = re.compile(r"^\s*box\b", re.IGNORECASE)
_LEADING_NUM = re.compile(r"^\s*(?:[0-9]+(?:\.[0-9]+)*)\s*[-–—.:]?\s*")
_LEADING_THE = re.compile(r"^the\s+")
_TRAILING_PUNCT = re.compile(r"[\s.:;,\-–—]+$")


def clean_heading(heading: str) -> str:
    t = normalize_title(heading or "")
    if not t:
        return ""
    t = _LEADING_NUM.sub("", t).strip()
    t = _FOOTNOTE_MARK.sub("", t).strip()
    t = _LEADING_THE.sub("", t).strip()
    return _TRAILING_PUNCT.sub("", t).strip()


def is_box(heading: str) -> bool:
    """True when a (normalized) heading is an analytical box — never mined."""
    return bool(_BOX_PREFIX.match(heading or ""))


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=\.)\s+", text or "") if part.strip()]


# ---------------------------------------------------------------------------
# explicit value claims (structural) — a percentage becomes a Fact only behind
# an explicit value-claim verb; a forecast without a reference period is
# under-determined and ignored; share/ratio units are never percentages.
# ---------------------------------------------------------------------------
_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"
_MONTH_NUM = {
    "january": "01", "february": "02", "march": "03", "april": "04", "may": "05", "june": "06",
    "july": "07", "august": "08", "september": "09", "october": "10", "november": "11", "december": "12",
}
_QUARTER_NUM = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}
VALUE_TOKEN = rf"{_RATE_ITEM}\s*(?:%|per\s+cent)"

_VALUE_GATE = re.compile(
    r"(?:projected|expected|forecast)\s*(?:to\s+)?(?:average|stand\s+at|be|reach|amount\s+to|"
    r"grow\s+by|grow\s+from|expand\s+by|expand\s+from|increase\s+(?:by|from|to)|rise\s+(?:by|from|to)|"
    r"decline\s+to|decline\s+from|fall\s+to|fall\s+from|remain\s+at)\s+"
    r"|(?:stood at|standing at|averaged|was at|were at|is at|are at|stands at|running at|remain(?:s|ed|ing)? at|"
    r"declined to|declined by|declined from|fell to|fell by|fell from|dropped to|dropped by|dropped from|"
    r"rose to|rose by|rose from|increased to|increased by|increased from|"
    r"expanded by|grew by|contracted by|narrowed to|widened to|reached)\s+",
    re.IGNORECASE,
)
_FORECAST_VERB = re.compile(r"\b(?:projected|expected|forecast)\b", re.IGNORECASE)
_PERCEPT_WITH_QUARTER = re.compile(
    rf"(?P<token>{VALUE_TOKEN})\s+(?P<period>in|during)\s+(?:the\s+)?(?P<quarter>first|second|third|fourth)\s+quarter\s+of\s+(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_PERCEPT_WITH_MONTH = re.compile(
    rf"(?P<token>{VALUE_TOKEN})\s+(?P<period>in|during)\s+(?P<month>{_MONTH_WORDS})\s+(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_PERCEPT_WITH_YEAR = re.compile(
    rf"(?P<token>{VALUE_TOKEN})\s+(?P<period>in|during|by|for)\s+(?:the\s+)?(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_VALUE_TOKEN_ONLY = re.compile(rf"(?P<token>{VALUE_TOKEN})", re.IGNORECASE)
_SHARE_UNIT = re.compile(
    r"\bof\s+(?:gdp|gross\s+domestic\s+product|total|disposable\s+income|labour\s+(?:force|income))\b",
    re.IGNORECASE,
)
# A GDP deflator / per-capita mention is a distinct measure and never a GDP value.
_GDP_NEAR_MISS = re.compile(
    r"(?:\bgdp\b\s+(?:deflator|per\s+capita)\b|per\s+capita\s+\bgdp\b)",
    re.IGNORECASE,
)


def token_value(token: str) -> float:
    return float(re.match(r"[0-9.]+", token).group(0))


def is_share(sentence: str, match: re.Match) -> bool:
    window = sentence[match.end("token") : match.end("token") + 80]
    return bool(_SHARE_UNIT.search(window))


def period_for(match: re.Match) -> FactPeriod | None:
    gd = match.groupdict()
    if "year" not in gd or not gd.get("year"):
        return None
    year = gd["year"]
    if "quarter" in gd and gd.get("quarter"):
        return FactPeriod(
            PeriodKind.QUARTER,
            f"{year}-{_QUARTER_NUM[gd['quarter'].lower()]}",
            label=sentence_label(match),
        )
    if "month" in gd and gd.get("month"):
        return FactPeriod(
            PeriodKind.MONTH,
            f"{year}-{_MONTH_NUM[gd['month'].lower()]}",
            label=sentence_label(match),
        )
    return FactPeriod(PeriodKind.YEAR, year, label=sentence_label(match))


def sentence_label(match: re.Match) -> str:
    gd = match.groupdict()
    start = match.start("period") if gd.get("period") else match.start("token")
    end = match.end()
    return match.string[start:end]


class Reporter:
    """Deterministic, provenance-carrying fact emitter with within-run dedup.

    One instance per extractor run. ``extraction_version`` / ``bank_tag`` are
    structural configuration supplied by the bank-specific extractor (the
    identity qualifier prefix is bank-scoped so ids stay unique per document).
    """

    def __init__(self, *, extraction_version: str, bank_tag: str) -> None:
        self.version = extraction_version
        self.bank_tag = bank_tag
        self.counters: dict[tuple, int] = {}
        self.seen: set[tuple] = set()

    @staticmethod
    def _dedup_key(subject: str, predicate: str, period: FactPeriod | None, value: FactValue, source_text: str) -> tuple:
        period_key = period.canonical() if period else ""
        if value.kind in (ValueKind.NUMBER, ValueKind.PERCENTAGE, ValueKind.BASIS_POINTS, ValueKind.CURRENCY):
            return (subject, predicate, period_key, value.value)
        return (subject, predicate, period_key, normalize_title(source_text or ""))

    def emit(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        source_text: str,
        subject: str,
        predicate: str,
        value: FactValue,
        confidence: Confidence,
        *,
        period: FactPeriod | None = None,
        location: FactLocation | None = None,
        method: str = METHOD_REGEX,
    ) -> Fact | None:
        if self._dedup_key(subject, predicate, period, value, source_text) in self.seen:
            return None
        self.seen.add(self._dedup_key(subject, predicate, period, value, source_text))

        period_key = period.canonical() if period else ""
        key = (subject, predicate, period_key)
        ordinal = self.counters.get(key, 0)
        self.counters[key] = ordinal + 1

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
            extraction_version=self.version,
            confidence=confidence,
            speaker=None,
            identity_qualifier=f"report:{subject}:{ordinal}",
        )

    def emit_text(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        subject: str,
        predicate: str,
    ) -> None:
        fact = self.emit(
            result, document, index, sentence, subject, predicate,
            FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
            Confidence.MEDIUM,
        )
        if fact is not None:
            result.add(fact)

    def emit_risk(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        subject: str,
    ) -> None:
        """Risk sentence: categorical orientation when explicit, else verbatim."""
        lower = sentence.lower()
        orientation = None
        if re.search(r"\b(?:broadly\s+)?balanced\b|\btwo-sided\b|\bsymmetric\b", lower):
            orientation = "balanced"
        elif re.search(r"\bdownside\b", lower):
            orientation = "downside"
        elif re.search(r"\bupside\b", lower):
            orientation = "upside"
        if orientation is not None:
            fact = self.emit(
                result, document, index, sentence, subject, PREDICATE_ASSESSMENT,
                categorical(orientation, source_text=sentence), Confidence.HIGH,
            )
        else:
            fact = self.emit(
                result, document, index, sentence, subject, PREDICATE_ASSESSMENT,
                FactValue(ValueKind.TEXT, value=sentence, source_text=sentence), Confidence.MEDIUM,
            )
        if fact is not None:
            result.add(fact)

    def emit_value_facts(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        subject: str,
    ) -> int:
        """Emit percentage value facts for explicit value claims.

        A percentage followed by an explicit reference period (year / month /
        quarter) keeps that period; a percentage without one is kept without a
        period unless it is a *forecast*, which is under-determined and
        ignored. Share/ratio units ("% of GDP") are never percentages. Returns
        the number of facts emitted (nonzero also when a genuine claim was
        present but deduped/ignored, so the caller keeps the sentence).
        """
        if not _VALUE_GATE.search(sentence):
            return 0
        forecast = bool(_FORECAST_VERB.search(sentence))
        covered: list[tuple[int, int]] = []
        emitted = 0
        claimed = False

        for finder in (_PERCEPT_WITH_QUARTER, _PERCEPT_WITH_MONTH, _PERCEPT_WITH_YEAR, _VALUE_TOKEN_ONLY):
            for match in finder.finditer(sentence):
                if any(start <= match.start("token") and match.end("token") <= end for start, end in covered):
                    continue
                if is_share(sentence, match):
                    continue
                claimed = True
                covered.append((match.start("token"), match.end("token")))
                period = period_for(match)
                if period is None and forecast:
                    continue  # a forecast without a reference period is ignored
                token = match.group("token")
                fact = self.emit(
                    result, document, index, sentence, subject, PREDICATE_VALUE,
                    percentage(token_value(token), source_text=token), Confidence.HIGH,
                    period=period,
                )
                if fact is not None:
                    result.add(fact)
                    emitted += 1
        return emitted if emitted else (1 if claimed else 0)
