"""Norges Bank — Monetary Policy Report + mixed-content extractor (Phase 10).

Norges Bank's "Monetary Policy Report" (MPR) is a **mixed-content** document: it
carries both the macroeconomic narrative (inflation, growth, labour market,
risks) and the published **policy-rate path** — the projected future level of
the policy rate used in the projections. This extractor handles both faces of
the document, following the Phase 10 rule of **precision over recall**:

- economic sections are routed deterministically by normalized heading; an
  unknown section is never mined for bare assessments
- a quantitative value fact is produced only behind an explicit value claim
  ("expected/averaged/stood at … per cent") with an explicit reference year
  where the source names one
- the policy-rate path is extracted only from sentences that explicitly put a
  numeric policy-rate level in a future year ("a policy rate of 1.90 per cent
  in 2028")
- risks are categorical orientations (upside / downside / balanced) or verbatim

Deliberately NOT extracted (Phase 5/9 boundary):

- the current policy-rate decision and its date (Phase 5, gated on decision
  publications) — the *current* "the policy rate is …" sentence never becomes a
  ``policy_rate_projection`` fact (projections require a future reference year)
- the full projection tables (Phase 9 tables), individual votes (Phase 8),
  hawkish/dovish interpretation
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
from ..normalize import normalize_title, parse_datetime
from .base import ReportsExtractor

EXTRACTION_VERSION = "10.1.0"

SUBJECT_INFLATION = "inflation"
SUBJECT_CORE_INFLATION = "core_inflation"
SUBJECT_INFLATION_EXPECTATIONS = "inflation_expectations"
SUBJECT_GROWTH = "growth"
SUBJECT_GDP = "gdp"
SUBJECT_UNEMPLOYMENT = "unemployment"
SUBJECT_WAGES = "wages"
SUBJECT_INFLATION_RISK = "inflation_risk"
SUBJECT_GROWTH_RISK = "growth_risk"
SUBJECT_RISK = "risk"
SUBJECT_POLICY_RATE_PROJECTION = "policy_rate_projection"

PREDICATE_ASSESSMENT = "assessment"
PREDICATE_STATEMENT = "statement"
PREDICATE_VALUE = "value"
PREDICATE_DATE = "date"

_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"
_VALUE_TOKEN = rf"{_RATE_ITEM}\s*(?:%|per\s+cent)"

_DATE_TOKEN = re.compile(
    rf"\b(?:[0-9]{{1,2}}\s+(?:{_MONTH_WORDS})\s+[0-9]{{4}}|(?:{_MONTH_WORDS})\s+[0-9]{{1,2}},\s+[0-9]{{4}})\b",
    re.IGNORECASE,
)

CAT_INTRO = "intro"
CAT_LABOUR = "labour_market"
CAT_RISK = "risk"
CAT_INFLATION = "inflation"
CAT_GROWTH = "growth"
CAT_GUIDANCE = "guidance"
CAT_UNCLASSIFIED = "unclassified"

_INTRO_HEADINGS = frozenset({
    "introduction",
    "executive summary",
    "the main economic assessment",
    "monetary policy assessment",
    "monetary policy report",
    "main economic assessment",
})
_RISK_HEADINGS = frozenset({"risks", "risk assessment", "uncertainty", "risks and uncertainty"})
_GUIDANCE_HEADINGS = frozenset({"monetary policy", "policy stance", "monetary policy stance", "forward guidance"})
_INFLATION_HEADINGS = frozenset({"inflation", "inflation outlook", "inflation and monetary policy", "prices and costs"})
_GROWTH_HEADINGS = frozenset({
    "the norwegian economy",
    "the international economy",
    "economic outlook",
    "economic activity",
    "the norwegian economy and international economy",
})
_LABOUR_HEADINGS = frozenset({"the labour market", "labour market", "employment", "wages", "unemployment"})

_LEADING_NUM = re.compile(r"^\s*(?:[0-9]+(?:\.[0-9]+)*)\s*[-–—.:]?\s*")
_FOOTNOTE_MARK = re.compile(r"\s*(?:\(\d+\)|\[\d+\]|\d+\)|[*†‡]+)\s*$")
_LEADING_THE = re.compile(r"^the\s+")
_TRAILING_PUNCT = re.compile(r"[\s.:;,\-–—]+$")

# Value claim: explicit "expected/stood at … per cent" phrasing. Norges also
# writes "was 3.7 per cent" (bare verb) — covered by the final alternatives with
# a lookahead for the numeral, which the task-aligned precision rule allows.
_VALUE_GATE = re.compile(
    r"(?:projected|expected|forecast)\s*(?:to\s+)?(?:average|stand\s+at|be|reach|amount\s+to|grow\s+by|expand\s+by|remain)\s+"
    r"|(?:stood at|was at|were at|is at|are at|stands at|running at|remain(?:s|ed)? at|declined to|fell to|"
    r"dropped to|rose to|increased to|registered|reached)\s+"
    r"|(?:was|were|is|are|will\s+be|will\s+average|will\s+reach)\s+(?=[0-9])",
    re.IGNORECASE,
)

_PERCEPT_WITH_YEAR = re.compile(
    rf"(?P<token>{_VALUE_TOKEN})\s+(?:in|for)\s+(?:the\s+)?(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_VALUE_TOKEN_ONLY = re.compile(rf"(?P<token>{_VALUE_TOKEN})", re.IGNORECASE)

# The policy-rate path: an explicit numeric policy-rate level in a future year.
_RATE_PATH = re.compile(
    rf"\bpolicy\s+rate\b[^.]*?\b{_VALUE_TOKEN}\s+(?:in|during|by|at\s+the\s+end\s+of)\s+(?:the\s+)?20[0-9]{{2}}\b",
    re.IGNORECASE,
)

_RISK_ORIENTATION = re.compile(
    r"\b(?:downside|upside|two-sided|symmetric|broadly\s+balanced)\s+risks?\b"
    r"|\brisks?\b[^.]*\b(?:are|were|is|remain(?:s|ed)?)\s+(?:broadly\s+)?balanced\b"
    r"|\btilted\s+(?:more\s+)?(?:to\s+(?:the\s+)?)?(?:downside|upside)\b"
    r"|\buncertain(?:ty|ties)?\b",
    re.IGNORECASE,
)


def _clean_heading(heading: str) -> str:
    t = normalize_title(heading or "")
    if not t:
        return ""
    t = _LEADING_NUM.sub("", t).strip()
    t = _FOOTNOTE_MARK.sub("", t).strip()
    t = _LEADING_THE.sub("", t).strip()
    return _TRAILING_PUNCT.sub("", t).strip()


def _section_category(heading: str) -> str:
    t = _clean_heading(heading)
    if not t:
        return CAT_UNCLASSIFIED
    if t in _INTRO_HEADINGS:
        return CAT_INTRO
    if t in _RISK_HEADINGS:
        return CAT_RISK
    if t in _GUIDANCE_HEADINGS:
        return CAT_GUIDANCE
    if t in _INFLATION_HEADINGS:
        return CAT_INFLATION
    if t in _GROWTH_HEADINGS:
        return CAT_GROWTH
    if t in _LABOUR_HEADINGS:
        return CAT_LABOUR
    return CAT_UNCLASSIFIED


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=\.)\s+", text or "") if part.strip()]


class NorgesReportExtractor(ReportsExtractor):
    bank = "norges"
    extraction_version = EXTRACTION_VERSION

    def extract(self, publication, document: NormalizedDocument) -> ExtractionResult:
        result = ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )
        if not document.sections:
            result.warnings.append("no_sections")
            return result

        date_fact = self._date_fact(result, document)
        if date_fact is not None:
            fact, skipped_index = date_fact
            result.add(fact)
        else:
            result.warnings.append("no_reference_date")
            skipped_index = -1

        counters: dict[str, int] = {}
        risk_found = False
        rate_path_found = False

        for index, section in enumerate(document.sections):
            if index == skipped_index:
                continue
            category = _section_category(section.heading or "")
            for sentence in _split_sentences(section.text or ""):
                if _RATE_PATH.search(sentence):
                    rate_path_found = True
                    self._add_rate_path(result, document, index, sentence, counters)
                    continue
                if _RISK_ORIENTATION.search(sentence):
                    risk_found = True
                    self._add_risk_facts(result, document, index, sentence, counters)
                    continue
                if category in (CAT_INTRO, CAT_UNCLASSIFIED):
                    # Precision over recall: only explicit value claims are mined
                    # from intro / unknown sections (the MPR's mixed content),
                    # never bare assessments.
                    self._add_value_claims(result, document, index, sentence, counters)
                    continue
                if category == CAT_GUIDANCE:
                    result.add(self._text_fact(result, document, index, sentence, "policy_guidance", PREDICATE_STATEMENT, counters))
                elif category == CAT_RISK:
                    risk_found = True
                    self._add_risk_facts(result, document, index, sentence, counters)
                elif category == CAT_INFLATION:
                    self._add_inflation_facts(result, document, index, sentence, counters)
                elif category == CAT_GROWTH:
                    self._add_growth_facts(result, document, index, sentence, counters)
                elif category == CAT_LABOUR:
                    self._add_labour_facts(result, document, index, sentence, counters)

        if not risk_found:
            result.warnings.append("no_risk_assessment")
        if not rate_path_found:
            result.warnings.append("no_rate_path")
        return result

    # ------------------------------------------------------------------
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

    @classmethod
    def _text_fact(cls, result, document, index: int, sentence: str, subject: str, predicate: str, counters: dict) -> Fact:
        return cls._fact(
            result, document, index, sentence, subject, predicate,
            FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
            Confidence.MEDIUM, counters,
        )

    @staticmethod
    def _token_value(token: str) -> float:
        return float(re.match(r"[0-9.]+", token).group(0))

    # ------------------------------------------------------------------
    def _date_fact(self, result, document):
        for index, section in enumerate(document.sections):
            for candidate in (section.heading or "", section.text or ""):
                match = _DATE_TOKEN.search(candidate)
                if not match:
                    continue
                dt = parse_datetime(match.group(0))
                if dt is None:
                    continue
                iso = dt.date().isoformat()
                return (
                    Fact(
                        publication_id=result.publication_id,
                        document_id=document.document_id,
                        subject="monetary_policy",
                        predicate=PREDICATE_DATE,
                        value=FactValue(ValueKind.DATE, value=iso, source_text=match.group(0)),
                        effective_date=dt,
                        source_location=FactLocation(LocationKind.SECTION, section=index),
                        source_text=match.group(0),
                        extraction_method=METHOD_REGEX,
                        extraction_version=EXTRACTION_VERSION,
                        confidence=Confidence.HIGH,
                    ),
                    index,
                )
        return None

    # ------------------------------------------------------------------
    def _add_rate_path(self, result, document, index: int, sentence: str, counters: dict) -> None:
        """Mixed-content: an explicit policy-rate level in a future year becomes a
        ``policy_rate_projection/value`` fact with that year as reference period.
        The current policy rate (no future year) is never a projection."""
        for match in _PERCEPT_WITH_YEAR.finditer(sentence):
            token = match.group("token")
            period = FactPeriod(PeriodKind.YEAR, match.group("year"), label=match.group(0))
            result.add(
                self._fact(
                    result, document, index, sentence, SUBJECT_POLICY_RATE_PROJECTION, PREDICATE_VALUE,
                    percentage(self._token_value(token), source_text=token),
                    Confidence.HIGH, counters,
                    period=period,
                )
            )

    # ------------------------------------------------------------------
    def _add_value_claims(self, result, document, index: int, sentence: str, counters: dict) -> None:
        """Gated value claims from intro / unknown sections (the MPR's mixed
        content). Bare assessments — and unknown subjects — are never emitted
        here (precision over recall)."""
        if not _VALUE_GATE.search(sentence):
            return
        subject = self._classify_subject(sentence)
        if subject is None:
            return
        if self._add_value_facts(result, document, index, sentence, subject, counters):
            return
        result.add(self._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters))

    @staticmethod
    def _classify_subject(sentence: str) -> str | None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            return SUBJECT_INFLATION_EXPECTATIONS
        if "underlying inflation" in lower or "core inflation" in lower or "cpi-ate" in lower:
            return SUBJECT_CORE_INFLATION
        if "policy rate" in lower:
            return None  # explicit policy-rate levels are the rate-path pre-catch
        if "unemployment" in lower:
            return SUBJECT_UNEMPLOYMENT
        if "wage" in lower:
            return SUBJECT_WAGES
        if "inflation" in lower or "prices" in lower or "cpi" in lower:
            return SUBJECT_INFLATION
        if "gdp" in lower or "growth" in lower or "activity" in lower or "economy" in lower:
            return SUBJECT_GDP
        return None

    # ------------------------------------------------------------------
    def _add_risk_facts(self, result, document, index: int, sentence: str, counters: dict) -> None:
        lower = sentence.lower()
        if "inflation" in lower or "prices" in lower:
            subject = SUBJECT_INFLATION_RISK
        elif "growth" in lower or "activity" in lower or "gdp" in lower or "economy" in lower:
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
                self._fact(
                    result, document, index, sentence, subject, PREDICATE_ASSESSMENT,
                    categorical(orientation, source_text=sentence),
                    Confidence.HIGH, counters,
                )
            )
        else:
            result.add(self._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters))

    # ------------------------------------------------------------------
    def _add_value_facts(self, result, document, index: int, sentence: str, subject: str, counters: dict) -> int:
        if not _VALUE_GATE.search(sentence):
            return 0
        covered: list[tuple[int, int]] = []
        emitted = 0
        finders = (_PERCEPT_WITH_YEAR, _VALUE_TOKEN_ONLY)
        for finder in finders:
            for match in finder.finditer(sentence):
                if any(start <= match.start("token") and match.end("token") <= end for start, end in covered):
                    continue
                token = match.group("token")
                period = None
                if finder is _PERCEPT_WITH_YEAR:
                    period = FactPeriod(PeriodKind.YEAR, match.group("year"), label=match.group(0))
                covered.append((match.start("token"), match.end("token")))
                result.add(
                    self._fact(
                        result, document, index, sentence, subject, PREDICATE_VALUE,
                        percentage(self._token_value(token), source_text=token),
                        Confidence.HIGH, counters,
                        period=period,
                    )
                )
                emitted += 1
        return emitted

    def _add_inflation_facts(self, result, document, index: int, sentence: str, counters: dict) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        elif "underlying inflation" in lower or "core inflation" in lower or "cpi-ate" in lower:
            subject = SUBJECT_CORE_INFLATION
        elif "inflation" in lower or "prices" in lower or "cpi" in lower:
            subject = SUBJECT_INFLATION
        else:
            # unknown subject inside a gated value claim: precision over recall
            return
        if self._add_value_facts(result, document, index, sentence, subject, counters):
            return
        result.add(self._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters))

    def _add_growth_facts(self, result, document, index: int, sentence: str, counters: dict) -> None:
        lower = sentence.lower()
        if "gdp" in lower or "activity" in lower:
            subject = SUBJECT_GDP
        else:
            subject = SUBJECT_GROWTH
        if self._add_value_facts(result, document, index, sentence, subject, counters):
            return
        result.add(self._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters))

    def _add_labour_facts(self, result, document, index: int, sentence: str, counters: dict) -> None:
        lower = sentence.lower()
        if "unemployment" in lower:
            subject = SUBJECT_UNEMPLOYMENT
        elif "wage" in lower:
            subject = SUBJECT_WAGES
        else:
            subject = "labour_market"
        if self._add_value_facts(result, document, index, sentence, subject, counters):
            return
        result.add(self._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters))


__all__ = [
    "EXTRACTION_VERSION",
    "SUBJECT_INFLATION",
    "SUBJECT_CORE_INFLATION",
    "SUBJECT_INFLATION_EXPECTATIONS",
    "SUBJECT_GROWTH",
    "SUBJECT_GDP",
    "SUBJECT_UNEMPLOYMENT",
    "SUBJECT_WAGES",
    "SUBJECT_INFLATION_RISK",
    "SUBJECT_GROWTH_RISK",
    "SUBJECT_RISK",
    "SUBJECT_POLICY_RATE_PROJECTION",
    "NorgesReportExtractor",
]