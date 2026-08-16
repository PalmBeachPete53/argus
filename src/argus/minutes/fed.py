"""Fed — FOMC minutes extractor (Phase 4.4).

Extracts the facts of a Federal Reserve "Minutes of the Federal Open Market
Committee" from the normalized document text, answering "what did the FOMC
explicitly say or discuss during the meeting?":

- the staff economic and financial review, and the staff outlook;
- the discussion of the economic outlook and monetary policy — economic
  conditions, inflation, the labour market, growth, risks and policy
  considerations;
- forward guidance, verbatim.

Phase 4.4 specifics — attribution (Fed vocabulary):

- Attribution is what the source states, never invented. ``Fact.speaker`` stays
  ``None``; the attribution the minutes do state — ``dissent`` / ``one_member``
  / ``some_members`` / ``most_members`` / ``members`` / ``staff`` /
  ``committee`` / ``collective`` — is preserved in ``identity_qualifier``
  (``minutes:{attribution}:{n}``). "Participants", "many participants",
  "one participant", "the staff", "the Committee" all map to their stated
  attribution (Fed wording for the ECB's "members"/"council" family).
- Discussion wording is handled faithfully: "Participants discussed the
  possibility of further rate adjustments" states no position and produces no
  fact; "Several participants noted that inflation was expected to average
  2.0 percent in 2027" is mined normally.

Deliberately NOT extracted (Phase 4.4 boundary):

- the decision itself — the "Committee's Policy Actions" section (the target
  range, rate changes, the effective date and the vote record) is Phase 4.1
  territory and is routed to ``CAT_IGNORE``; the minutes extractor never
  produces decision/rate facts
- hawkish/dovish interpretation, market expectations, probability conversion
- Phases 4.5–4.7 (the SEP projections, the reports, speeches)

An unknown section is never assumed to be economic: "absence of proof →
absence of extraction".
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
from .base import MinutesExtractor

EXTRACTION_VERSION = "8.1.0"

SUBJECT_INFLATION = "inflation"
SUBJECT_CORE_INFLATION = "core_inflation"
SUBJECT_INFLATION_EXPECTATIONS = "inflation_expectations"
SUBJECT_GROWTH = "growth"
SUBJECT_GDP = "gdp"
SUBJECT_LABOUR_MARKET = "labour_market"
SUBJECT_UNEMPLOYMENT = "unemployment"
SUBJECT_WAGES = "wages"
SUBJECT_MONETARY_POLICY = "monetary_policy"
SUBJECT_RISK = "risk"
SUBJECT_INFLATION_RISK = "inflation_risk"
SUBJECT_GROWTH_RISK = "growth_risk"
SUBJECT_FINANCIAL_CONDITIONS = "financial_conditions"
SUBJECT_POLICY_GUIDANCE = "policy_guidance"

PREDICATE_ASSESSMENT = "assessment"
PREDICATE_STATEMENT = "statement"
PREDICATE_VALUE = "value"

CAT_IGNORE = "ignore"
CAT_GENERAL = "general"
CAT_POLICY = "policy"
CAT_RISK = "risk"
CAT_FINANCIAL = "financial"
CAT_INFLATION = "inflation"
CAT_LABOUR = "labour"
CAT_GROWTH = "growth"

# Known Fed sections — mined. Section identity is exact on the normalized
# heading; anything not listed (including a future appendix) is ignored.
_MINE_HEADINGS = frozenset({
    "staff review of the economic situation",
    "staff review of the financial situation",
    "staff economic outlook",
    "outlook for economic activity and prices",
    "recent developments in financial market conditions",
    "discussion of the economic outlook and monetary policy",
    "discussion of the economic outlook and monetary policy and the committee's policy actions",
})
# Decision / vote-record / communications sections — the Phase 4.1 decision, the
# record of the policy action and the meeting logistics are never mined here.
_IGNORE_HEADING_PREFIXES = (
    "minutes of",
    "committee's policy actions",
    "updates to the committee's statement",
    "changes to the committee's statement",
    "communications",
    "information received",
)
# Section headings holding the decision / vote / statement content, matched by
# exact identity.
_DECISION_HEADINGS = frozenset({
    "the policy action",
    "committee's vote",
    "voting record",
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
    t = _clean_heading(heading)
    if not t:
        return CAT_IGNORE
    if t.startswith(_IGNORE_HEADING_PREFIXES):
        return CAT_IGNORE
    if t in _DECISION_HEADINGS:
        return CAT_IGNORE
    if t in _MINE_HEADINGS:
        return CAT_GENERAL
    return CAT_IGNORE


# ---------------------------------------------------------------------------
# Attribution — Fed vocabulary. ``Fact.speaker`` stays ``None``; the attribution
# the minutes state is preserved in ``identity_qualifier``.
# ---------------------------------------------------------------------------
ATTR_DISSENT = "dissent"
ATTR_ONE_MEMBER = "one_member"
ATTR_SOME_MEMBERS = "some_members"
ATTR_MOST_MEMBERS = "most_members"
ATTR_MEMBERS = "members"
ATTR_STAFF = "staff"
ATTR_COMMITTEE = "committee"
ATTR_COLLECTIVE = "collective"

_ATTR_DISSENT = re.compile(r"\b(?:dissented?|dissenting|voting against|voted against|voting no)\b", re.IGNORECASE)
_ATTR_ONE_MEMBER = re.compile(
    r"\bone participant\b|\ba single participant\b|\bone member\b|\ba participant\b|\ba couple of participants\b",
    re.IGNORECASE,
)
_ATTR_SOME_MEMBERS = re.compile(
    r"\bsome participants\b|\bseveral participants\b|\ba number of participants\b|\ba few participants\b|"
    r"\bsome members\b|\bseveral members\b",
    re.IGNORECASE,
)
_ATTR_MOST_MEMBERS = re.compile(
    r"\bmost participants\b|\bmany participants\b|\ba majority of participants\b|\bmost members\b",
    re.IGNORECASE,
)
_ATTR_MEMBERS = re.compile(r"\bparticipants\b|\bmembers\b", re.IGNORECASE)
_ATTR_STAFF = re.compile(r"\bthe staff\b|\bstaff\b|\bstaff review\b|\bstaff forecast\b", re.IGNORECASE)
_ATTR_COMMITTEE = re.compile(r"\bthe\s+committee\b|\bthe\s+fomc\b", re.IGNORECASE)


def _attribution(sentence: str) -> str:
    if _ATTR_DISSENT.search(sentence):
        return ATTR_DISSENT
    if _ATTR_ONE_MEMBER.search(sentence):
        return ATTR_ONE_MEMBER
    if _ATTR_SOME_MEMBERS.search(sentence):
        return ATTR_SOME_MEMBERS
    if _ATTR_MOST_MEMBERS.search(sentence):
        return ATTR_MOST_MEMBERS
    if _ATTR_STAFF.search(sentence):
        return ATTR_STAFF
    if _ATTR_MEMBERS.search(sentence):
        return ATTR_MEMBERS
    if _ATTR_COMMITTEE.search(sentence):
        return ATTR_COMMITTEE
    return ATTR_COLLECTIVE


_META_DISCUSSION = re.compile(
    r"\b(?:the\s+)?discussion\s+(?:focused|centred|centered)\s+on\b"
    r"|\b(?:the\s+)?topic\s+of\s+(?:the\s+)?discussion\b"
    r"|\bdiscuss(?:ed|ing)\s+(?:the\s+)?(?:possibility|implications?|prospects?|options?)\b",
    re.IGNORECASE,
)

CAT_NONE = "none"
CAT_GUIDANCE = "guidance"

_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(?:stand|stands|stood)\s+ready\s+to\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+not\s+hesitate\s+to\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+keep\s+(?:the\s+)?(?:federal\s+funds\s+)?rate\s+target\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+not\s+pre-?commit\b", re.IGNORECASE),
    re.compile(r"\bmeeting\s+by\s+meeting\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+be\s+guided\s+by\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+depend\s+on\b", re.IGNORECASE),
    re.compile(r"\bdata-?dependent\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+(?:assess|monitor)\s+(?:the\s+)?(?:incoming\s+)?data\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+require\s+greater\s+confidence\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+be\s+appropriate\s+to\s+loosen\b", re.IGNORECASE),
    re.compile(r"\bfuture\s+(?:policy\s+)?decisions\b", re.IGNORECASE),
)

_POLICY_TERM = re.compile(
    r"\b(?:policy|monetary|rate|rates|interest rates?|federal funds rate|committee|target range)\b",
    re.IGNORECASE,
)
_POLICY_STANCE = re.compile(
    r"\bstance\b"
    r"|\bappropriate\b"
    r"|\brestrictive\b"
    r"|\baccommodative\b"
    r"|\btightening\b"
    r"|\beasing\b"
    r"|\bloosening\b"
    r"|\bpreferred\b",
    re.IGNORECASE,
)
_POLICY_STANCE_PHRASE = re.compile(
    r"\b(?:appropriate|restrictive|accommodative|neutral|loose|tight)\s+stance\b"
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
    re.compile(r"\bfinancial market conditions\b", re.IGNORECASE),
    re.compile(r"\bcredit\b", re.IGNORECASE),
    re.compile(r"\bbank lending\b", re.IGNORECASE),
    re.compile(r"\bspreads?\b", re.IGNORECASE),
    re.compile(r"\bmarket rates?\b", re.IGNORECASE),
    re.compile(r"\bborrowing costs?\b", re.IGNORECASE),
    re.compile(r"\bterm premiums?\b", re.IGNORECASE),
    re.compile(r"\bfinancial stability\b", re.IGNORECASE),
)

_INFLATION_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\binflation\b", re.IGNORECASE),
    re.compile(r"\bdisinflation\b", re.IGNORECASE),
    re.compile(r"\bprice pressures\b", re.IGNORECASE),
    re.compile(r"\benergy prices\b", re.IGNORECASE),
    re.compile(r"\bfood prices\b", re.IGNORECASE),
    re.compile(r"\bprices\b", re.IGNORECASE),
)

_LABOUR_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\blabour market\b", re.IGNORECASE),
    re.compile(r"\blabor market\b", re.IGNORECASE),
    re.compile(r"\bunemployment\b", re.IGNORECASE),
    re.compile(r"\bemployment\b", re.IGNORECASE),
    re.compile(r"\bjob gains\b", re.IGNORECASE),
    re.compile(r"\bwage\b", re.IGNORECASE),
)

_GROWTH_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bgrowth\b", re.IGNORECASE),
    re.compile(r"\bgdp\b", re.IGNORECASE),
    re.compile(r"\bactivity\b", re.IGNORECASE),
    re.compile(r"\beconom(?:y|ic)\b", re.IGNORECASE),
    re.compile(r"\boutput\b", re.IGNORECASE),
    re.compile(r"\bdemand\b", re.IGNORECASE),
    re.compile(r"\bconsumption\b", re.IGNORECASE),
    re.compile(r"\binvestment\b", re.IGNORECASE),
    re.compile(r"\bproduction\b", re.IGNORECASE),
    re.compile(r"\brecovery\b", re.IGNORECASE),
    re.compile(r"\bexpansion\b", re.IGNORECASE),
)

_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"
_MONTH_NUM = {
    "january": "01", "february": "02", "march": "03", "april": "04", "may": "05", "june": "06",
    "july": "07", "august": "08", "september": "09", "october": "10", "november": "11", "december": "12",
}
_VALUE_TOKEN = rf"{_RATE_ITEM}\s*(?:%|per\s+cent|percent)"

_VALUE_GATE = re.compile(
    r"(?:projected|expected|forecast)\s+(?:to\s+)?(?:average|stand\s+at|be|reach|amount\s+to|grow\s+by|expand\s+by)\s+"
    r"|(?:stood at|averaged|was at|were at|is at|are at|stands at|running at|remain(?:s|ed)? at|declined to|fell to|"
    r"dropped to|rose to|increased to|reached)\s+",
    re.IGNORECASE,
)
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
    return [part.strip() for part in re.split(r"(?<=\.)\s+", text or "") if part.strip()]


class _RunState:
    __slots__ = ("risk_found", "guidance_found")

    def __init__(self) -> None:
        self.risk_found = False
        self.guidance_found = False


class FedMinutesExtractor(MinutesExtractor):
    bank = "fed"
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
        state = _RunState()

        for index, section in enumerate(document.sections):
            if _section_category(section.heading or "") == CAT_IGNORE:
                continue
            self._process_section(result, document, index, section.text or "", counters, state)

        if not state.risk_found:
            result.warnings.append("no_risk_assessment")
        if not state.guidance_found:
            result.warnings.append("no_forward_guidance")
        return result

    def _process_section(
        self, result, document, index: int, text: str, counters: dict, state: _RunState,
    ) -> None:
        for sentence in _split_sentences(text):
            self._mine_sentence(result, document, index, sentence, counters, state)

    def _mine_sentence(
        self, result, document, index: int, sentence: str, counters: dict, state: _RunState,
    ) -> None:
        if _META_DISCUSSION.search(sentence):
            return
        category = self._categorize(sentence)
        attribution = _attribution(sentence)
        context = f"minutes:{attribution}"
        if category == CAT_GUIDANCE:
            state.guidance_found = True
            result.add(self._text_fact(result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, PREDICATE_STATEMENT, counters, context))
        elif category == CAT_POLICY:
            result.add(self._text_fact(result, document, index, sentence, SUBJECT_MONETARY_POLICY, PREDICATE_STATEMENT, counters, context))
        elif category == CAT_RISK:
            state.risk_found = True
            self._add_risk_facts(result, document, index, sentence, counters, context)
        elif category == CAT_FINANCIAL:
            if not self._add_value_facts(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, counters, context):
                result.add(self._text_fact(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, PREDICATE_ASSESSMENT, counters, context))
        elif category == CAT_INFLATION:
            self._add_inflation_facts(result, document, index, sentence, counters, context)
        elif category == CAT_LABOUR:
            self._add_labour_facts(result, document, index, sentence, counters, context)
        elif category == CAT_GROWTH:
            self._add_growth_facts(result, document, index, sentence, counters, context)

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
        return CAT_NONE

    @staticmethod
    def _matches(anchors: tuple[re.Pattern, ...], sentence: str) -> bool:
        return any(anchor.search(sentence) for anchor in anchors)

    @classmethod
    def _text_fact(cls, result, document, index, sentence, subject, predicate, counters, context) -> Fact:
        return cls._fact(
            result, document, index, sentence, subject, predicate,
            FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
            Confidence.MEDIUM, counters, context=context,
        )

    @staticmethod
    def _fact(result, document, index: int, sentence: str, subject: str, predicate: str, value, confidence, counters: dict, *, period=None, context: str = "") -> Fact:
        ordinal = counters.get(context, 0)
        counters[context] = ordinal + 1
        qualifier = f"{context}:{ordinal}" if context else ""
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
            speaker=None,
            identity_qualifier=qualifier,
        )

    # ------------------------------------------------------------------
    @classmethod
    def _add_risk_facts(cls, result, document, index: int, sentence: str, counters: dict, context: str) -> None:
        lower = sentence.lower()
        if "inflation" in lower or "prices" in lower:
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
                    Confidence.HIGH, counters, context=context,
                )
            )
        else:
            result.add(cls._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters, context))

    # ------------------------------------------------------------------
    @classmethod
    def _add_value_facts(cls, result, document, index: int, sentence: str, subject: str, counters: dict, context: str) -> int:
        if not _VALUE_GATE.search(sentence):
            return 0
        covered: list[tuple[int, int]] = []
        emitted = 0
        for match in _PERCENT_WITH_MONTH.finditer(sentence):
            token = match.group("token")
            period = FactPeriod(
                PeriodKind.MONTH,
                f"{match.group('year')}-{_MONTH_NUM[match.group('month').lower()]}",
                label=sentence[match.start("period"): match.end()],
            )
            covered.append((match.start("token"), match.end("token")))
            result.add(
                cls._fact(
                    result, document, index, sentence, subject, PREDICATE_VALUE,
                    percentage(cls._token_value(token), source_text=token),
                    Confidence.HIGH, counters, period=period, context=context,
                )
            )
            emitted += 1
        for match in _PERCENT_WITH_YEAR.finditer(sentence):
            token = match.group("token")
            period = FactPeriod(
                PeriodKind.YEAR,
                match.group("year"),
                label=sentence[match.start("period"): match.end()],
            )
            covered.append((match.start("token"), match.end("token")))
            result.add(
                cls._fact(
                    result, document, index, sentence, subject, PREDICATE_VALUE,
                    percentage(cls._token_value(token), source_text=token),
                    Confidence.HIGH, counters, period=period, context=context,
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
                    Confidence.HIGH, counters, context=context,
                )
            )
            emitted += 1
        return emitted

    @staticmethod
    def _token_value(token: str) -> float:
        return float(re.match(r"[0-9.]+", token).group(0))

    # ------------------------------------------------------------------
    @classmethod
    def _add_inflation_facts(cls, result, document, index: int, sentence: str, counters: dict, context: str) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        elif "core inflation" in lower:
            subject = SUBJECT_CORE_INFLATION
        else:
            subject = SUBJECT_INFLATION
        if cls._add_value_facts(result, document, index, sentence, subject, counters, context):
            return
        result.add(cls._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters, context))

    @classmethod
    def _add_growth_facts(cls, result, document, index: int, sentence: str, counters: dict, context: str) -> None:
        if cls._add_value_facts(result, document, index, sentence, SUBJECT_GDP, counters, context):
            return
        result.add(cls._text_fact(result, document, index, sentence, SUBJECT_GROWTH, PREDICATE_ASSESSMENT, counters, context))

    @classmethod
    def _add_labour_facts(cls, result, document, index: int, sentence: str, counters: dict, context: str) -> None:
        lower = sentence.lower()
        if "unemployment" in lower:
            subject = SUBJECT_UNEMPLOYMENT
        elif "wage" in lower:
            subject = SUBJECT_WAGES
        else:
            subject = SUBJECT_LABOUR_MARKET
        if cls._add_value_facts(result, document, index, sentence, subject, counters, context):
            return
        result.add(cls._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters, context))


__all__ = [
    "EXTRACTION_VERSION",
    "SUBJECT_INFLATION",
    "SUBJECT_CORE_INFLATION",
    "SUBJECT_INFLATION_EXPECTATIONS",
    "SUBJECT_GROWTH",
    "SUBJECT_GDP",
    "SUBJECT_LABOUR_MARKET",
    "SUBJECT_UNEMPLOYMENT",
    "SUBJECT_WAGES",
    "SUBJECT_MONETARY_POLICY",
    "SUBJECT_RISK",
    "SUBJECT_INFLATION_RISK",
    "SUBJECT_GROWTH_RISK",
    "SUBJECT_FINANCIAL_CONDITIONS",
    "SUBJECT_POLICY_GUIDANCE",
    "PREDICATE_ASSESSMENT",
    "PREDICATE_STATEMENT",
    "PREDICATE_VALUE",
    "ATTR_DISSENT",
    "ATTR_ONE_MEMBER",
    "ATTR_SOME_MEMBERS",
    "ATTR_MOST_MEMBERS",
    "ATTR_MEMBERS",
    "ATTR_STAFF",
    "ATTR_COMMITTEE",
    "ATTR_COLLECTIVE",
    "FedMinutesExtractor",
]