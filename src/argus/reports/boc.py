"""Bank of Canada — Monetary Policy Report extractor (Phase 4.x).

The BoC's quarterly **Monetary Policy Report** is a large narrative
publication: it sets out the outlook for growth in Canada and abroad (GDP),
the outlook for inflation, the labour market, financial conditions, risks to
the inflation and growth outlooks and the monetary policy rationale.

This extractor follows the Report-family **precision-over-recall** rule:

- sections are routed deterministically by normalized heading — known economic
  headings are mined (content-first), while the report masthead, known
  non-economic headings and **unknown** headings are ignored
  (`UNKNOWN ≠ ECONOMIC`);
- sentence classification is content-first with a fixed precedence:
  guidance > policy > risk > financial > inflation > labour > growth;
- a quantitative value Fact is produced only behind an explicit value-claim
  verb with a percentage and, for a forecast, an explicit reference period;
- risks are categorical orientations when stated, verbatim otherwise.

Deliberately NOT extracted (Phase 4.1/8/9 boundary): the policy interest-rate
decision (Phase 4.1), the projection tables (Phase 4.5) and hawkish/dovish
interpretation.
"""

from __future__ import annotations

import re

from ..documents.base import NormalizedDocument
from ..facts import ExtractionResult
from ._shared import (
    SUBJECT_FINANCIAL_CONDITIONS,
    SUBJECT_GDP,
    SUBJECT_GROWTH,
    SUBJECT_GROWTH_RISK,
    SUBJECT_INFLATION,
    SUBJECT_INFLATION_EXPECTATIONS,
    SUBJECT_INFLATION_RISK,
    SUBJECT_LABOUR_MARKET,
    SUBJECT_MONETARY_POLICY,
    SUBJECT_POLICY_GUIDANCE,
    SUBJECT_RISK,
    SUBJECT_UNEMPLOYMENT,
    SUBJECT_WAGES,
    Reporter,
    clean_heading,
    is_box,
    split_sentences,
)
from .base import ReportsExtractor

EXTRACTION_VERSION = "10.3.0"

CAT_IGNORE = "ignore"
CAT_UNKNOWN = "unknown"
CAT_GENERAL = "general"
CAT_POLICY = "policy"
CAT_RISK = "risk"
CAT_FINANCIAL = "financial"
CAT_INFLATION = "inflation"
CAT_LABOUR = "labour"
CAT_GROWTH = "growth"

_IGNORE_HEADINGS = frozenset({
    "monetary policy report", "contents", "foreword", "editorial", "glossary",
    "references", "bibliography", "annex", "appendix", "methodology",
    "legal notice", "disclaimer", "notes", "list of boxes", "list of tables",
})
_POLICY_HEADINGS = frozenset({
    "monetary policy", "monetary policy stance", "policy stance",
    "monetary policy and the outlook", "the framework for conducting monetary policy",
})
_RISK_HEADINGS = frozenset({
    "risks", "risk assessment", "risks to the outlook", "risks to the inflation outlook",
    "risks to the growth outlook", "risk scenario", "the risks",
})
_INFLATION_HEADINGS = frozenset({
    "inflation", "the outlook for inflation", "outlook for inflation", "inflation in canada",
    "prices and costs", "costs and prices", "inflation and the labour market",
})
_LABOUR_HEADINGS = frozenset({
    "the labour market", "labour market", "employment", "wages", "unemployment",
})
_FINANCIAL_HEADINGS = frozenset({
    "financial conditions", "financial system", "financial markets",
    "money and credit", "financial developments", "financial stability",
})
_GROWTH_HEADINGS = frozenset({
    "the outlook for growth", "outlook for growth", "growth in canada and abroad",
    "the canadian economy", "canadian economy",
    "economic activity", "economic outlook", "gdp", "the global economy",
    "the world economy", "world economy", "the international economy", "international economy",
    "global economy",
})
_GENERAL_HEADINGS = frozenset({
    "summary", "executive summary", "overview", "introduction", "economic analysis",
})

_GUIDANCE_ANCHORS = (
    re.compile(r"\bstands?\s+ready\s+to\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bwould\s+be\s+prepared\s+to\b", re.IGNORECASE),
    re.compile(r"\bmonetary\s+policy\s+will\s+need\s+to\b", re.IGNORECASE),
    re.compile(r"\b(?:future\s+)?(?:policy\s+)?(?:decisions?|monetary\s+policy)\s+(?:will|would)\s+depend\s+on\b", re.IGNORECASE),
    re.compile(r"\bdata[\s-]?dependent\b", re.IGNORECASE),
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess)\b", re.IGNORECASE),
)
_POLICY_TERM = re.compile(
    r"\b(?:monetary\s+policy|policy\s+rate|interest\s+rates?|governing\s+council|"
    r"bank of canada|policy\s+interest\s+rate)\b",
    re.IGNORECASE,
)
_POLICY_STANCE = re.compile(
    r"\bdecided\s+to\b|\bdecision(?:s)?\b|\bstance\b|\bappropriate\b|\brestrictive\b|\baccommodative\b|\btightening\b|\beasing\b",
    re.IGNORECASE,
)
_RISK_ANCHORS = (
    re.compile(r"\brisks?\s+(?:to|for|around|surrounding|from|of|are|were|remain|remained|have|has)\b", re.IGNORECASE),
    re.compile(r"\b(?:downside|upside|two-sided|symmetric|broadly\s+balanced)\s+risks?\b", re.IGNORECASE),
    re.compile(r"\btilted\b", re.IGNORECASE),
    re.compile(r"\buncertain(?:ty|ties)?\b", re.IGNORECASE),
)
_FINANCIAL_ANCHORS = (
    re.compile(r"\bfinancial conditions\b", re.IGNORECASE),
    re.compile(r"\bfinancing conditions?\b", re.IGNORECASE),
    re.compile(r"\bcredit conditions\b", re.IGNORECASE),
    re.compile(r"\b(?:bank\s+lending|lending\s+(?:rates?|growth|to|conditions?))\b", re.IGNORECASE),
    re.compile(r"\b(?:yield|credit|sovereign|bond|rate)\s+spreads?\b", re.IGNORECASE),
    re.compile(r"\bmarket rates?\b", re.IGNORECASE),
    re.compile(r"\bfinancial stability\b", re.IGNORECASE),
)
_INFLATION_ANCHORS = (
    re.compile(r"\binflation\b", re.IGNORECASE),
    re.compile(r"\bdisinflation\b", re.IGNORECASE),
    re.compile(r"\bcpi\b", re.IGNORECASE),
    re.compile(r"\bprice pressures\b", re.IGNORECASE),
    re.compile(r"\benergy prices\b", re.IGNORECASE),
    re.compile(r"\bfood prices\b", re.IGNORECASE),
)
_LABOUR_ANCHORS = (
    re.compile(r"\blabour market\b", re.IGNORECASE),
    re.compile(r"\blabor market\b", re.IGNORECASE),
    re.compile(r"\bunemployment\b", re.IGNORECASE),
    re.compile(r"\bwage(?:s)?\b(?!\s+policy\b)", re.IGNORECASE),
)
_GROWTH_ANCHORS = (
    re.compile(r"\bgrowth\b", re.IGNORECASE),
    re.compile(r"(?<!per\scapita\s)\bgdp\b(?!\s+(?:deflator|per\s+capita)\b)", re.IGNORECASE),
    re.compile(r"\b(?:economic\s+)?activity\b", re.IGNORECASE),
    re.compile(r"\b(?:real\s+output|output\s+growth|output\s+gaps?|potential\s+output)\b", re.IGNORECASE),
    re.compile(r"\bconsumption\b", re.IGNORECASE),
    re.compile(r"\binvestment\b", re.IGNORECASE),
    re.compile(r"\beconom(?:y|ic)\b", re.IGNORECASE),
)

_GDP_NEAR_MISS = re.compile(r"(?:\bgdp\b\s+(?:deflator|per\s+capita)\b|per\s+capita\s+\bgdp\b)", re.IGNORECASE)


def _section_category(heading: str) -> str:
    t = clean_heading(heading or "")
    if not t or is_box(t):
        return CAT_IGNORE
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
    if t in _GENERAL_HEADINGS:
        return CAT_GENERAL
    return CAT_UNKNOWN


class BocReportExtractor(ReportsExtractor):
    bank = "boc"
    extraction_version = EXTRACTION_VERSION

    def extract(self, publication, document: NormalizedDocument) -> ExtractionResult:
        result = ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )
        if not document.sections:
            result.warnings.append("no_sections")
            return result

        reporter = Reporter(extraction_version=EXTRACTION_VERSION, bank_tag="boc")
        risk_found = False
        guidance_found = False
        economic_processed = False

        def matches(anchors, sentence) -> bool:
            return any(a.search(sentence) for a in anchors)

        def categorize(sentence: str) -> str:
            if matches(_GUIDANCE_ANCHORS, sentence):
                return CAT_POLICY
            if bool(_POLICY_STANCE.search(sentence) and _POLICY_TERM.search(sentence)):
                return CAT_POLICY
            if matches(_RISK_ANCHORS, sentence):
                return CAT_RISK
            if matches(_FINANCIAL_ANCHORS, sentence):
                return CAT_FINANCIAL
            if matches(_INFLATION_ANCHORS, sentence):
                return CAT_INFLATION
            if matches(_LABOUR_ANCHORS, sentence):
                return CAT_LABOUR
            if matches(_GROWTH_ANCHORS, sentence):
                return CAT_GROWTH
            return "none"

        for index, section in enumerate(document.sections):
            category = _section_category(section.heading or "")
            if category in (CAT_IGNORE, CAT_UNKNOWN):
                continue
            economic_processed = True
            for sentence in split_sentences(section.text or ""):
                cat = categorize(sentence)
                if cat == "none":
                    continue
                if cat == CAT_RISK:
                    risk_found = True
                    reporter.emit_risk(result, document, index, sentence, self._risk_subject(sentence))
                    continue
                if cat == CAT_POLICY:
                    if matches(_GUIDANCE_ANCHORS, sentence):
                        guidance_found = True
                        reporter.emit_text(result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, "statement")
                    else:
                        reporter.emit_text(result, document, index, sentence, SUBJECT_MONETARY_POLICY, "statement")
                    continue
                if cat == CAT_FINANCIAL:
                    if not reporter.emit_value_facts(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS):
                        reporter.emit_text(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, "assessment")
                    continue
                if cat == CAT_INFLATION:
                    self._inflation(result, reporter, document, index, sentence)
                    continue
                if cat == CAT_LABOUR:
                    self._labour(result, reporter, document, index, sentence)
                    continue
                if cat == CAT_GROWTH:
                    self._growth(result, reporter, document, index, sentence)
                    continue

        if not economic_processed:
            result.warnings.append("no_economic_sections")
        if not risk_found:
            result.warnings.append("no_risk_assessment")
        if not guidance_found:
            result.warnings.append("no_forward_guidance")
        return result

    @staticmethod
    def _risk_subject(sentence: str) -> str:
        lower = sentence.lower()
        if "inflation" in lower or "prices" in lower or "cpi" in lower:
            return SUBJECT_INFLATION_RISK
        if "growth" in lower or "activity" in lower or "gdp" in lower:
            return SUBJECT_GROWTH_RISK
        return SUBJECT_RISK

    @staticmethod
    def _inflation(result, reporter, document, index, sentence) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        elif "cpi" in lower or "inflation" in lower:
            subject = SUBJECT_INFLATION
        else:
            return
        if not reporter.emit_value_facts(result, document, index, sentence, subject):
            reporter.emit_text(result, document, index, sentence, subject, "assessment")

    @staticmethod
    def _labour(result, reporter, document, index, sentence) -> None:
        lower = sentence.lower()
        if "unemployment" in lower:
            subject = SUBJECT_UNEMPLOYMENT
        elif "wage" in lower:
            subject = SUBJECT_WAGES
        else:
            subject = SUBJECT_LABOUR_MARKET
        if not reporter.emit_value_facts(result, document, index, sentence, subject):
            reporter.emit_text(result, document, index, sentence, subject, "assessment")

    @staticmethod
    def _growth(result, reporter, document, index, sentence) -> None:
        if _GDP_NEAR_MISS.search(sentence):
            return
        if not reporter.emit_value_facts(result, document, index, sentence, SUBJECT_GDP):
            reporter.emit_text(result, document, index, sentence, SUBJECT_GROWTH, "assessment")


__all__ = [
    "EXTRACTION_VERSION",
    "BocReportExtractor",
]
