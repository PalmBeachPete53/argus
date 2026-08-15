"""Bank of Japan — Minutes of the Monetary Policy Meeting extractor (Phase 8).

Extracts the facts of a Bank of Japan "Minutes of the Monetary Policy Meeting"
from the normalized document, answering "what did the Policy Board explicitly
say or discuss during the meeting?":

- the economic and financial developments discussion — economic activity, the
  price situation (CPI), the labour market, financial conditions;
- the summary of the Policy Board's discussions on economic and financial
  developments and on monetary policy;
- forward guidance, verbatim.

Phase 8 specifics — attribution (BoJ vocabulary):

- ``Fact.speaker`` stays ``None``; the attribution the minutes state —
  ``dissent`` / ``one_member`` / ``some_members`` / ``most_members`` /
  ``members`` / ``committee`` / ``collective`` — is preserved in
  ``identity_qualifier`` (``minutes:{attribution}:{n}``). "The Policy Board",
  "the Board", "some members", "Most members", "one member", "a majority of
  members" all map to their stated attribution.

Discussion wording is handled faithfully: "Members discussed the outlook for
economic activity" states no position and produces no fact; "Some members
pointed out that the year-on-year rate of change in the CPI was expected to be
around 2 percent in fiscal 2026" is mined.

Deliberately NOT extracted (Phase 8 boundary):

- the decision itself — the uncollateralized overnight call rate target, the
  decision wording and the vote are the Statement's own content (BoJ fuses
  decision + statement) and are Phase 6 territory
- the Outlook for Economic Activity and Prices (projections) — Phase 9
- hawkish/dovish interpretation
- Phases 5/7/10/11 (decisions, press conferences, reports, speeches)

An unknown section is never assumed to be economic: "absence of proof →
absence of extraction".
"""

from __future__ import annotations

import re

from ..documents.base import NormalizedDocument
from ..facts import ExtractionResult
from ._shared import (
    ATTR_COLLECTIVE,
    ATTR_COMMITTEE,
    ATTR_DISSENT,
    ATTR_MEMBERS,
    ATTR_MOST_MEMBERS,
    ATTR_ONE_MEMBER,
    ATTR_SOME_MEMBERS,
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
    MinutesReporter,
    _META_DISCUSSION,
    clean_heading,
    is_box,
    split_sentences,
)
from .base import MinutesExtractor

EXTRACTION_VERSION = "8.3.0"

CAT_IGNORE = "ignore"
CAT_UNKNOWN = "unknown"
CAT_GENERAL = "general"
CAT_GUIDANCE = "guidance"
CAT_POLICY = "policy"
CAT_RISK = "risk"
CAT_FINANCIAL = "financial"
CAT_INFLATION = "inflation"
CAT_LABOUR = "labour"
CAT_GROWTH = "growth"

# Known BoJ minutes sections — mined. Identity is exact on the cleaned heading;
# anything not listed (including a future appendix) is ignored.
_MINE_HEADINGS = frozenset({
    "introduction",
    "i. economic and financial developments",
    "i economic and financial developments",
    "economic and financial developments",
    "a. economic developments",
    "a economic developments",
    "economic developments",
    "b. financial developments",
    "b financial developments",
    "financial developments",
    "ii. summary of the policy board's discussions on economic and financial developments",
    "ii summary of the policy board's discussions on economic and financial developments",
    "summary of the policy board's discussions on economic and financial developments",
    "a. summary of the policy board's discussions on economic and financial developments",
    "summary of the policy board's discussions",
    "a. summary of discussions",
    "summary of discussions",
    "b. summary of the policy board's discussions on the conduct of monetary policy",
    "summary of the policy board's discussions on the conduct of monetary policy",
    "the conduct of monetary policy",
    "iii. other matters discussed by the policy board",
    "iii other matters discussed by the policy board",
})
# Known BoJ non-economic / decision sections — ignored.
_IGNORE_HEADINGS = frozenset({
    "minutes of the monetary policy meeting",
    "bank of japan's policy board",
    "policy board members",
    "attendance",
    "the bank of japan's policy board",
    "appendix",
    "annex",
    "notes",
    "statistical data",
    "legal notice",
    "copyright",
})

_IGNORE_PREFIXES = (
    "minutes of",
    "box ",
    "box:",
)

_LEADING_TITLE_DATE = re.compile(r"^minutes of the monetary policy meeting(?: at .*)?$", re.IGNORECASE)

_ATTR_DISSENT = re.compile(r"\b(?:dissented?|dissenting|voting against|voted against|voting no)\b", re.IGNORECASE)
_ATTR_ONE_MEMBER = re.compile(r"\bone member\b|\ba single member\b|\ba member\b|\bone of the members\b", re.IGNORECASE)
_ATTR_SOME_MEMBERS = re.compile(r"\bsome members\b|\bseveral members\b|\ba number of members\b|\ba few members\b", re.IGNORECASE)
_ATTR_MOST_MEMBERS = re.compile(r"\bmost members\b|\bmany members\b|\ba majority of members\b", re.IGNORECASE)
_ATTR_MEMBERS = re.compile(r"\bmembers\b", re.IGNORECASE)
_ATTR_COMMITTEE = re.compile(r"\bthe policy board\b|\bthe board\b", re.IGNORECASE)

_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(?:will|would)\s+continue\s+(?:to\s+)?(?:with\s+)?(?:monetary\s+easing|quantitative|easing)\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bstands?\s+ready\s+to\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+not\s+hesitate\s+to\s+take\s+additional\b", re.IGNORECASE),
    re.compile(r"\bdata[\s-]?dependent\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+be\s+guided\s+by\b", re.IGNORECASE),
)
_POLICY_TERM = re.compile(r"\b(?:policy|monetary|rate|rates|interest rates?|bank|board|policy board)\b", re.IGNORECASE)
_POLICY_STANCE = re.compile(
    r"\bstance\b|\bappropriate\b|\brestrictive\b|\baccommodative\b|\btightening\b|\beasing\b|\bloosening\b|\bpreferred\b",
    re.IGNORECASE,
)
_RISK_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\brisks?\s+(?:to|for|around|surrounding|from|of|are|were|remain|remained|have|has)\b", re.IGNORECASE),
    re.compile(r"\b(?:downside|upside|two-sided|symmetric|broadly\s+balanced)\s+risks?\b", re.IGNORECASE),
    re.compile(r"\buncertain(?:ty|ties)?\b", re.IGNORECASE),
    re.compile(r"\btilted\b", re.IGNORECASE),
)
_FINANCIAL_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bfinancial conditions\b", re.IGNORECASE),
    re.compile(r"\bfinancing conditions?\b", re.IGNORECASE),
    re.compile(r"\bcredit\b", re.IGNORECASE),
    re.compile(r"\bbank lending\b", re.IGNORECASE),
    re.compile(r"\bfinancial market\b", re.IGNORECASE),
    re.compile(r"\byield\b", re.IGNORECASE),
    re.compile(r"\bspreads?\b", re.IGNORECASE),
    re.compile(r"\bforeign exchange\b", re.IGNORECASE),
)
_INFLATION_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\binflation\b", re.IGNORECASE),
    re.compile(r"\bcpi\b", re.IGNORECASE),
    re.compile(r"\bprice\b", re.IGNORECASE),
    re.compile(r"\bprices\b", re.IGNORECASE),
    re.compile(r"\bdisinflation\b", re.IGNORECASE),
    re.compile(r"\benergy prices\b", re.IGNORECASE),
    re.compile(r"\bfood prices\b", re.IGNORECASE),
)
_LABOUR_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\blabour market\b", re.IGNORECASE),
    re.compile(r"\blabor market\b", re.IGNORECASE),
    re.compile(r"\bunemployment\b", re.IGNORECASE),
    re.compile(r"\bemployment\b", re.IGNORECASE),
    re.compile(r"\bwage\b", re.IGNORECASE),
    re.compile(r"\bjob\b", re.IGNORECASE),
)
_GROWTH_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bgrowth\b", re.IGNORECASE),
    re.compile(r"(?<!per\scapita\s)\bgdp\b(?!\s+(?:deflator|per\s+capita)\b)", re.IGNORECASE),
    re.compile(r"\b(?:economic\s+)?activity\b", re.IGNORECASE),
    re.compile(r"\b(?:real\s+output|output\s+growth|output\s+gaps?|potential\s+output)\b", re.IGNORECASE),
    re.compile(r"\beconom(?:y|ic)\b", re.IGNORECASE),
    re.compile(r"\bexports?\b", re.IGNORECASE),
    re.compile(r"\bconsumption\b", re.IGNORECASE),
    re.compile(r"\binvestment\b", re.IGNORECASE),
)


def _section_category(heading: str) -> str:
    t = clean_heading(heading or "")
    if not t or is_box(t):
        return CAT_IGNORE
    if t.startswith(_IGNORE_PREFIXES):
        return CAT_IGNORE
    if t in _IGNORE_HEADINGS or _LEADING_TITLE_DATE.search(t):
        return CAT_IGNORE
    if t in _MINE_HEADINGS:
        return CAT_GENERAL
    return CAT_UNKNOWN


def _attribution(sentence: str) -> str:
    if _ATTR_DISSENT.search(sentence):
        return ATTR_DISSENT
    if _ATTR_ONE_MEMBER.search(sentence):
        return ATTR_ONE_MEMBER
    if _ATTR_SOME_MEMBERS.search(sentence):
        return ATTR_SOME_MEMBERS
    if _ATTR_MOST_MEMBERS.search(sentence):
        return ATTR_MOST_MEMBERS
    if _ATTR_MEMBERS.search(sentence):
        return ATTR_MEMBERS
    if _ATTR_COMMITTEE.search(sentence):
        return ATTR_COMMITTEE
    return ATTR_COLLECTIVE


class BojMinutesExtractor(MinutesExtractor):
    bank = "boj"
    extraction_version = EXTRACTION_VERSION

    def extract(self, publication, document: NormalizedDocument) -> ExtractionResult:
        result = ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )
        if not document.sections:
            result.warnings.append("no_sections")
            return result

        reporter = MinutesReporter(extraction_version=EXTRACTION_VERSION, bank_tag="boj")
        economic_processed = False

        def matches(anchors, sentence) -> bool:
            return any(a.search(sentence) for a in anchors)

        def categorize(sentence: str) -> str:
            if matches(_GUIDANCE_ANCHORS, sentence):
                return CAT_GUIDANCE
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
                if _META_DISCUSSION.search(sentence):
                    continue
                cat = categorize(sentence)
                attribution = _attribution(sentence)
                if cat == "none":
                    continue
                if cat == CAT_GUIDANCE:
                    reporter.guidance_found = True
                    reporter.emit_text(result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, "statement", attribution=attribution)
                elif cat == CAT_POLICY:
                    reporter.emit_text(result, document, index, sentence, SUBJECT_MONETARY_POLICY, "statement", attribution=attribution)
                elif cat == CAT_RISK:
                    reporter.risk_found = True
                    reporter.emit_risk(result, document, index, sentence, self._risk_subject(sentence), attribution=attribution)
                elif cat == CAT_FINANCIAL:
                    if not reporter.emit_value_facts(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, attribution=attribution):
                        reporter.emit_text(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, "assessment", attribution=attribution)
                elif cat == CAT_INFLATION:
                    self._inflation(result, reporter, document, index, sentence, attribution)
                elif cat == CAT_LABOUR:
                    self._labour(result, reporter, document, index, sentence, attribution)
                elif cat == CAT_GROWTH:
                    self._growth(result, reporter, document, index, sentence, attribution)

        if not economic_processed:
            result.warnings.append("no_economic_sections")
        if not reporter.risk_found:
            result.warnings.append("no_risk_assessment")
        if not reporter.guidance_found:
            result.warnings.append("no_forward_guidance")
        return result

    @staticmethod
    def _risk_subject(sentence: str) -> str:
        lower = sentence.lower()
        if "inflation" in lower or "prices" in lower or "cpi" in lower:
            return SUBJECT_INFLATION_RISK
        if "growth" in lower or "activity" in lower or "gdp" in lower or "economy" in lower:
            return SUBJECT_GROWTH_RISK
        return SUBJECT_RISK

    @staticmethod
    def _inflation(result, reporter, document, index, sentence, attribution) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        else:
            subject = SUBJECT_INFLATION
        if not reporter.emit_value_facts(result, document, index, sentence, subject, attribution=attribution):
            reporter.emit_text(result, document, index, sentence, subject, "assessment", attribution=attribution)

    @staticmethod
    def _labour(result, reporter, document, index, sentence, attribution) -> None:
        lower = sentence.lower()
        if "unemployment" in lower:
            subject = SUBJECT_UNEMPLOYMENT
        elif "wage" in lower:
            subject = SUBJECT_WAGES
        else:
            subject = SUBJECT_LABOUR_MARKET
        if not reporter.emit_value_facts(result, document, index, sentence, subject, attribution=attribution):
            reporter.emit_text(result, document, index, sentence, subject, "assessment", attribution=attribution)

    @staticmethod
    def _growth(result, reporter, document, index, sentence, attribution) -> None:
        if not reporter.emit_value_facts(result, document, index, sentence, SUBJECT_GDP, attribution=attribution):
            reporter.emit_text(result, document, index, sentence, SUBJECT_GROWTH, "assessment", attribution=attribution)


__all__ = [
    "EXTRACTION_VERSION",
    "BojMinutesExtractor",
]
