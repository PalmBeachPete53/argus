"""Shared machinery for Phase 6 bank-specific statement extractors.

The seven "long tail" statement extractors (fed, boe, boc, snb, rba, rbnz,
riksbank) all need the same extraction engine — the affine part is limited to
the bank's own vocabulary (section headings, guidance/risk/rationale anchors,
date format, value-claim phrasing). Keeping that machinery here instead of
duplicating it seven times is deliberate: the bank-specific wording — invariant
10 — still lives in each bank module, only the identical generic parts are
shared. The two flagship statement extractors (``ecb``, ``boj``) remain
self-contained, untouched canonical implementations.

The engine is a faithful generalization of the ECB statement extractor:

- sections are routed deterministically by normalized heading (intro / risk /
  guidance / inflation / growth / labour / financial / unclassified), with the
  narrow content-first fallback (guidance > risk > rationale) for sections whose
  heading carries no signal
- sentences are mined for categorical risk orientations (upside / downside /
  balanced) or kept as verbatim assessments
- quantitative value facts are produced only behind an explicit value claim
  ("projected/expected/averaged … at X%"), with a verbatim reference period
  when the source names one
- absence of a risk assessment / forward guidance is surfaced as a warning,
  never as an invented "balanced" / "no guidance" fact
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
from .base import StatementExtractor

# ---------------------------------------------------------------------------
# Canonical Phase 6 subjects (controlled vocabulary, shared by all statement
# extractors). ``policy_guidance`` is shared with Phase 5.
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
PREDICATE_DATE = "date"

_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"
_MONTH_NUM = {
    "january": "01", "february": "02", "march": "03", "april": "04", "may": "05", "june": "06",
    "july": "07", "august": "08", "september": "09", "october": "10", "november": "11", "december": "12",
}
_VALUE_TOKEN = rf"{_RATE_ITEM}\s*(?:%|per\s+cent|percent)"

CAT_INTRO = "intro"
CAT_FINANCIAL = "financial_conditions"
CAT_LABOUR = "labour_market"
CAT_RISK = "risk"
CAT_INFLATION = "inflation"
CAT_GROWTH = "growth"
CAT_GUIDANCE = "guidance"
CAT_UNCLASSIFIED = "unclassified"


class BankStatementExtractor(StatementExtractor):
    """Generic Phase 6 statement engine. Concrete banks defined the vocabulary
    below; everything else is shared."""

    # --- per-bank vocabulary (class attributes, overridden by banks) -------
    intro_headings: frozenset[str] = frozenset()
    risk_headings: frozenset[str] = frozenset()
    guidance_headings: frozenset[str] = frozenset()
    inflation_headings: frozenset[str] = frozenset()
    growth_headings: frozenset[str] = frozenset()
    labour_headings: frozenset[str] = frozenset()
    financial_headings: frozenset[str] = frozenset()

    guidance_anchors: tuple[re.Pattern, ...] = ()
    risk_anchors: tuple[re.Pattern, ...] = ()
    rationale_anchors: tuple[re.Pattern, ...] = ()

    value_gate: re.Pattern = re.compile(
        r"(?:projected|expected|forecast)\s+(?:to\s+)?(?:average|stand\s+at|be|reach|amount\s+to|grow\s+by|expand\s+by)\s+"
        r"|(?:stood at|averaged|was at|were at|is at|are at|stands at|running at|remain(?:s|ed)? at|declined to|fell to|"
        r"dropped to|rose to|increased to|reached)\s+",
        re.IGNORECASE,
    )
    percent_with_year: re.Pattern = re.compile(
        rf"(?P<token>{_VALUE_TOKEN})\s+(?P<period>in|during|by|for)\s+(?:the\s+)?(?P<year>20[0-9]{{2}})\b",
        re.IGNORECASE,
    )
    percent_with_month: re.Pattern = re.compile(
        rf"(?P<token>{_VALUE_TOKEN})\s+(?P<period>in|during)\s+(?P<month>{_MONTH_WORDS})\s+(?P<year>20[0-9]{{2}})\b",
        re.IGNORECASE,
    )
    value_token_only: re.Pattern = re.compile(rf"(?P<token>{_VALUE_TOKEN})", re.IGNORECASE)

    # "19 June 2026", "June 19, 2026" (US banks) or "4 August 2026".
    date_token: re.Pattern = re.compile(
        rf"\b(?:[0-9]{{1,2}}\s+(?:{_MONTH_WORDS})\s+[0-9]{{4}}|(?:{_MONTH_WORDS})\s+[0-9]{{1,2}},\s+[0-9]{{4}})\b",
        re.IGNORECASE,
    )

    # --- generic extraction --------------------------------------------------
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
            fact, date_index = date_fact
            result.add(fact)
        else:
            result.warnings.append("no_reference_date")

        counters: dict[str, int] = {}
        risk_found = False
        guidance_found = False

        for index, section in enumerate(document.sections):
            if index == date_index:
                continue
            category = self._section_category(section.heading or "")
            for sentence in self._split_sentences(section.text or ""):
                if category in (CAT_INTRO, CAT_UNCLASSIFIED):
                    if self._matches(self.guidance_anchors, sentence):
                        guidance_found = True
                        result.add(self._text_fact(result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, PREDICATE_STATEMENT, counters))
                    elif self._matches(self.risk_anchors, sentence):
                        risk_found = True
                        self._add_risk_facts(result, document, index, sentence, counters)
                    elif self._matches(self.rationale_anchors, sentence):
                        result.add(self._text_fact(result, document, index, sentence, SUBJECT_MONETARY_POLICY, PREDICATE_RATIONALE, counters))
                elif category == CAT_GUIDANCE:
                    guidance_found = True
                    result.add(self._text_fact(result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, PREDICATE_STATEMENT, counters))
                elif category == CAT_RISK:
                    risk_found = True
                    self._add_risk_facts(result, document, index, sentence, counters)
                elif category == CAT_FINANCIAL:
                    result.add(self._text_fact(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, PREDICATE_ASSESSMENT, counters))
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

    # --- routing -------------------------------------------------------------
    _LEADING_NUM = re.compile(r"^\s*(?:[0-9]+(?:\.[0-9]+)*)\s*[-–—.:]?\s*")
    _FOOTNOTE_MARK = re.compile(r"\s*(?:\(\d+\)|\[\d+\]|\d+\)|[*†‡]+)\s*$")
    _LEADING_THE = re.compile(r"^the\s+")
    _TRAILING_PUNCT = re.compile(r"[\s.:;,\-–—]+$")

    @classmethod
    def _clean_heading(cls, heading: str) -> str:
        t = normalize_title(heading or "")
        if not t:
            return ""
        t = cls._LEADING_NUM.sub("", t).strip()
        t = cls._FOOTNOTE_MARK.sub("", t).strip()
        t = cls._LEADING_THE.sub("", t).strip()
        return cls._TRAILING_PUNCT.sub("", t).strip()

    def _section_category(self, heading: str) -> str:
        t = self._clean_heading(heading)
        if not t:
            return CAT_UNCLASSIFIED
        if t in self.intro_headings:
            return CAT_INTRO
        if t in self.financial_headings:
            return CAT_FINANCIAL
        if t in self.labour_headings:
            return CAT_LABOUR
        if t in self.risk_headings:
            return CAT_RISK
        if t in self.inflation_headings:
            return CAT_INFLATION
        if t in self.growth_headings:
            return CAT_GROWTH
        if t in self.guidance_headings:
            return CAT_GUIDANCE
        return CAT_UNCLASSIFIED

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        return [part.strip() for part in re.split(r"(?<=\.)\s+", text or "") if part.strip()]

    @staticmethod
    def _matches(anchors: tuple[re.Pattern, ...], sentence: str) -> bool:
        return any(anchor.search(sentence) for anchor in anchors)

    # --- fact helpers ---------------------------------------------------------
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

    # --- date ---------------------------------------------------------------
    def _date_fact(self, result, document):
        for index, section in enumerate(document.sections):
            for candidate in (section.heading or "", section.text or ""):
                match = self.date_token.search(candidate)
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
                        subject=SUBJECT_MONETARY_POLICY,
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

    # --- risk assessment -----------------------------------------------------
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

    # --- quantitative value claims -------------------------------------------
    def _add_value_facts(self, result, document, index: int, sentence: str, subject: str, counters: dict) -> int:
        if not self.value_gate.search(sentence):
            return 0

        covered: list[tuple[int, int]] = []
        emitted = 0

        for match in self.percent_with_month.finditer(sentence):
            token = match.group("token")
            period = FactPeriod(
                PeriodKind.MONTH,
                f"{match.group('year')}-{_MONTH_NUM[match.group('month').lower()]}",
                label=sentence[match.start("period"): match.end()],
            )
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

        for match in self.percent_with_year.finditer(sentence):
            token = match.group("token")
            period = FactPeriod(
                PeriodKind.YEAR,
                match.group("year"),
                label=sentence[match.start("period"): match.end()],
            )
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

        for match in self.value_token_only.finditer(sentence):
            if any(start <= match.start("token") and match.end("token") <= end for start, end in covered):
                continue
            token = match.group("token")
            result.add(
                self._fact(
                    result, document, index, sentence, subject, PREDICATE_VALUE,
                    percentage(self._token_value(token), source_text=token),
                    Confidence.HIGH, counters,
                )
            )
            emitted += 1

        return emitted

    @staticmethod
    def _token_value(token: str) -> float:
        return float(re.match(r"[0-9.]+", token).group(0))

    # --- inflation / growth / labour market -----------------------------------
    def _add_inflation_facts(self, result, document, index: int, sentence: str, counters: dict) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        elif "core inflation" in lower or "underlying inflation" in lower:
            subject = SUBJECT_CORE_INFLATION
        else:
            subject = SUBJECT_INFLATION
        if self._add_value_facts(result, document, index, sentence, subject, counters):
            return
        result.add(self._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters))

    def _add_growth_facts(self, result, document, index: int, sentence: str, counters: dict) -> None:
        if self._add_value_facts(result, document, index, sentence, SUBJECT_GDP, counters):
            return
        result.add(self._text_fact(result, document, index, sentence, SUBJECT_GROWTH, PREDICATE_ASSESSMENT, counters))

    def _add_labour_facts(self, result, document, index: int, sentence: str, counters: dict) -> None:
        lower = sentence.lower()
        if "unemployment" in lower:
            subject = SUBJECT_UNEMPLOYMENT
        elif "wage" in lower:
            subject = SUBJECT_WAGES
        else:
            subject = SUBJECT_LABOUR_MARKET
        if self._add_value_facts(result, document, index, sentence, subject, counters):
            return
        result.add(self._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters))


EXTRACTION_VERSION = "6.1.0"