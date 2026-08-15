"""Reserve Bank of Australia — Board Minutes extractor (Phase 8).

Extracts the facts of an RBA "Minutes of the Monetary Policy Meeting of the
Board" / "Minutes of the Monetary Policy Board Meeting" from the normalized
document, answering "what did the Board explicitly say or discuss during the
meeting?":

- the economic discussion — financial conditions, domestic and international
  economic conditions, inflation, the labour market, growth and risks to the
  outlook;
- considerations for monetary policy and forward guidance, verbatim.

RBA-specific structural vocabulary:

- section headings — "Members present" / "Others present", "Financial
  conditions" / "Financial Markets", "Economic conditions" / "Domestic
  Economic Conditions" / "International Economic Conditions",
  "Considerations for Monetary Policy", "The Decision". Heading routing is by
  **exact identity** on the normalized heading; unknown sections are never
  assumed to be economic ("absence of proof → absence of extraction").
- attribution — the RBA minutes phrase positions as "the Board", "Members",
  "some members", "one member" or "a majority". Dissents are near-absent in
  the modern era, but the reverse-sentence patterns are kept so a recorded
  "dissented from the decision" is never misrouted. ``Fact.speaker`` stays
  ``None`` (a name is never invented); the stated attribution is preserved in
  ``identity_qualifier`` (``minutes:{attribution}:{n}``).
- the cash-rate decision itself — the target level, the change and the vote in
  "The Decision" — is Phase 5 territory (RBA ``monetary_policy_decision``) and
  is never priced here; the decision section is routed to IGNORE.

Deliberately NOT extracted (Phase 8 boundary): the policy decision and rate
level/vote, hawkish/dovish interpretation, and any Phase 6/7/9/10/11 content
(statements, press conferences, projections, reports, speeches).
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

EXTRACTION_VERSION = "8.6.0"

CAT_IGNORE = "ignore"
CAT_UNKNOWN = "unknown"
CAT_MINE = "mine"
CAT_GUIDANCE = "guidance"
CAT_POLICY = "policy"
CAT_RISK = "risk"
CAT_FINANCIAL = "financial"
CAT_INFLATION = "inflation"
CAT_LABOUR = "labour"
CAT_GROWTH = "growth"

# Known RBA minutes sections — mined. Identity is exact on the cleaned
# heading; anything not listed (including a future annex) is ignored.
_MINE_HEADINGS = frozenset({
    "financial conditions",
    "financial markets",
    "economic conditions",
    "domestic economic conditions",
    "international economic conditions",
    "considerations for monetary policy",
    "considerations for the monetary policy decision",
    "considerations for the policy decision",
})
# Known RBA non-economic / decision sections — ignored.
_IGNORE_HEADINGS = frozenset({
    "minutes of the monetary policy board meeting",
    "minutes of the monetary policy meeting of the reserve bank board",
    "minutes of the monetary policy meeting of the board",
    "members present",
    "others present",
    "the decision",
    "framework for additional monetary policy tools",
    "monetary policy board statement",
    "more on the monetary policy decision",
    "references",
    "sign up for email and sms alerts",
    "follow us",
    "affiliate sites",
    "annex",
    "appendix",
    "statistical appendix",
    "notes",
    "legal notice",
    "copyright",
})

_IGNORE_PREFIXES = (
    "minutes of",
    "box ",
    "box:",
)

_ATTR_DISSENT = re.compile(r"\b(?:dissented?|dissenting|voting against|voted against|reservation)\b", re.IGNORECASE)
_ATTR_ONE_MEMBER = re.compile(r"\bone member\b|\ba single member\b|\ba member\b|\bone of the members\b", re.IGNORECASE)
_ATTR_SOME_MEMBERS = re.compile(r"\bsome members\b|\bseveral members\b|\ba number of members\b|\ba few members\b", re.IGNORECASE)
_ATTR_MOST_MEMBERS = re.compile(r"\bmost members\b|\bmany members\b|\ba majority\b|\ba majority of\b|\bmajority of the board\b", re.IGNORECASE)
_ATTR_MEMBERS = re.compile(r"\bmembers\b", re.IGNORECASE)
_ATTR_COMMITTEE = re.compile(r"\bthe board\b|\bthe monetary policy board\b", re.IGNORECASE)

_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(?:stand|stands|stood)\s+ready\s+to\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+be\s+guided\s+by\b", re.IGNORECASE),
    re.compile(r"\b(?:future\s+)?(?:policy\s+)?(?:decisions?|monetary\s+policy)\s+(?:will|would)\s+depend\s+on\b", re.IGNORECASE),
    re.compile(r"\bdata[\s-]?dependent\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+remain\s+data[\s-]?dependent\b", re.IGNORECASE),
)
_POLICY_TERM = re.compile(r"\b(?:monetary policy|policy|policy rate|cash rate|interest rates?|the board)\b", re.IGNORECASE)
_POLICY_STANCE = re.compile(
    r"\bdecided\s+to\b|\bdecision(?:s)?\b|\bstance\b|\bappropriate\b|\brestrictive\b|\baccommodative\b|\btightening\b|\beasing\b|\bunchanged\b|\bpreferred\b",
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
    re.compile(r"\bfinancial markets?\b", re.IGNORECASE),
    re.compile(r"\bbond yields?\b", re.IGNORECASE),
    re.compile(r"\bcredit spreads?\b", re.IGNORECASE),
    re.compile(r"\bmarket rates?\b", re.IGNORECASE),
    re.compile(r"\bfinancial stability\b", re.IGNORECASE),
    re.compile(r"\bexchange rate\b", re.IGNORECASE),
)
_INFLATION_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\binflation\b", re.IGNORECASE),
    re.compile(r"\bcpi\b", re.IGNORECASE),
    re.compile(r"\bdisinflation\b", re.IGNORECASE),
    re.compile(r"\bunderlying inflation\b", re.IGNORECASE),
    re.compile(r"\bprice pressures\b", re.IGNORECASE),
    re.compile(r"\bprice\b", re.IGNORECASE),
    re.compile(r"\bprices\b", re.IGNORECASE),
)
_LABOUR_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\blabour market\b", re.IGNORECASE),
    re.compile(r"\blabor market\b", re.IGNORECASE),
    re.compile(r"\bunemployment\b", re.IGNORECASE),
    re.compile(r"\bemployment\b", re.IGNORECASE),
    re.compile(r"\bwage\b", re.IGNORECASE),
    re.compile(r"\b(?:spare\s+)?capacity\b", re.IGNORECASE),
)
_GROWTH_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bgrowth\b", re.IGNORECASE),
    re.compile(r"(?<!per\scapita\s)\bgdp\b(?!\s+(?:deflator|per\s+capita)\b)", re.IGNORECASE),
    re.compile(r"\b(?:economic\s+)?activity\b", re.IGNORECASE),
    re.compile(r"\b(?:real\s+output|output\s+growth|output\s+gaps?|potential\s+output)\b", re.IGNORECASE),
    re.compile(r"\bconsumption\b", re.IGNORECASE),
    re.compile(r"\binvestment\b", re.IGNORECASE),
    re.compile(r"\beconom(?:y|ic)\b", re.IGNORECASE),
    re.compile(r"\bexports?\b", re.IGNORECASE),
)


def _section_category(heading: str) -> str:
    t = clean_heading(heading or "")
    if not t or is_box(t):
        return CAT_IGNORE
    if t.startswith(_IGNORE_PREFIXES):
        return CAT_IGNORE
    if t in _IGNORE_HEADINGS:
        return CAT_IGNORE
    if t in _MINE_HEADINGS:
        return CAT_MINE
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


class RbaMinutesExtractor(MinutesExtractor):
    bank = "rba"
    extraction_version = EXTRACTION_VERSION

    def extract(self, publication, document: NormalizedDocument) -> ExtractionResult:
        result = ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )
        if not document.sections:
            result.warnings.append("no_sections")
            return result

        reporter = MinutesReporter(extraction_version=EXTRACTION_VERSION, bank_tag="rba")
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
        if "growth" in lower or "activity" in lower or "gdp" in lower:
            return SUBJECT_GROWTH_RISK
        return SUBJECT_RISK

    @staticmethod
    def _inflation(result, reporter, document, index, sentence, attribution) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        elif "underlying inflation" in lower or "trimmed mean" in lower or "weighted median" in lower:
            return  # underlying-inflation measures are not headline CPI
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
    "RbaMinutesExtractor",
]