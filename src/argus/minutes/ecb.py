"""ECB — Minutes / Meeting Account extractor (Phase 8).

Extracts the facts of an ECB "Account of the monetary policy meeting" from the
normalized document text, answering "what did the Governing Council explicitly
say or discuss during the meeting?":

- the **policy stance and policy considerations** discussion — the current
  stance, policy preferences and the arguments behind them;
- the **economic discussion** (external environment, real economy, prices and
  costs, money / credit / financial conditions) and the **risk assessment**;
- the **policy conclusions** and forward guidance.

Sections are routed conservatively by heading: a known economic heading is
mined, and an **unknown heading — or a known non-economic one (legal notice,
statistical annex, external monetary policy, the "Account of the monetary
policy meeting" title, "Minutes of …") — is ignored** ("absence of proof →
absence of extraction"). Known non-economic headings are matched by exact
identity on the cleaned heading plus the explicit title-style prefix families;
substring coincidence is never used. An unknown section is never assumed to be
economic, so a future appendix / glossary / disclaimer section yields no fact.

Content is classified sentence-by-sentence with the same deterministic
precedence as Phase 7 (guidance > policy > risk > financial > inflation >
labour > growth); the section heading only gates whether the section is mined
at all. A sentence matching no category produces no fact.

Phase 8 specifics — discussion wording and attribution:

- **Discussion wording is handled faithfully.** A sentence that only names the
  theme of the discussion without stating a position ("The discussion focused
  on inflation.", "Members discussed the possibility of further rate
  adjustments.") is **suppressed** — no fact is invented from "they discussed
  X". A sentence that states explicit content ("Members noted that inflation
  was expected to average 2.0% in 2027.") is mined as usual.
- **Attribution is what the source states, never invented.** ``Fact.speaker``
  is always ``None`` (the accounts do not reliably label individuals and a name
  is never guessed). The attribution the source *does* state — ``dissent``,
  ``one_member``, ``some_members``, ``members``, ``council`` or ``collective`` —
  is preserved in ``identity_qualifier`` (``minutes:{attribution}:{n}``), so
  individual positions and dissents are distinguished and traced without
  fabricating identities (roadmap Phase 8 criterion). An unmarked sentence is a
  collective statement of the account, never a named individual's.

Deliberately NOT extracted (Phase 8 boundary):

- the decision itself (wording, rates, changes, effective date) — Phase 5,
  gated on decision publications
- the decision rationale / macro analysis as a *statement* rationale — Phase 6
  territory
- votes: the ECB accounts do not report a formal vote count; a dissent is kept
  as the verbatim policy statement it is part of, with the ``dissent``
  attribution — never as an invented "n:y" count
- hawkish/dovish or stance interpretation, market expectations, forex
  fundamentals, probability conversion — none of these is ever invented here
- Phases 9–11 (projections, reports, speeches) — the qualitative economic
  discussion of the meeting account is mined, never a separate projection
  table.

Design rules

- No fact is invented. A value/orientation is only produced when the source
  states it, and every Fact preserves an *exact verbatim* supporting passage
  (``source_text``) copied from the normalized document.
- Quantitative facts carry ``FactPeriod`` only when the source states an
  explicit reference period; target phrasing is never mined as a value.
- Risk facts are categorical (``upside`` / ``downside`` / ``balanced``) only
  when the source states an explicit orientation; otherwise they are verbatim
  text assessments.
- Confidence is ``HIGH`` for quantitative percentages and categorical risk
  orientations; ``MEDIUM`` for verbatim text assessments/statements.
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

EXTRACTION_VERSION = "8.0.0"

# ---------------------------------------------------------------------------
# Canonical Phase 8 subjects (controlled vocabulary, see docs/EXTRACTORS.md).
# Reuses the Phase 5/6/7 subjects verbatim: the meeting account discusses the
# same content types, only the publication type differs.
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Section routing — CONSERVATIVE (Phase 8). A heading is mined only when it is
# a known economic section; an unknown heading and the known non-economic ones
# (title, legal notice, statistical annex, copyright, imprint, disclaimer,
# external monetary policy) are ignored. "Absence of proof → absence of
# extraction": an unknown future section never yields a fact.
# ---------------------------------------------------------------------------
CAT_IGNORE = "ignore"
CAT_GENERAL = "general"
CAT_POLICY = "policy"
CAT_RISK = "risk"
CAT_FINANCIAL = "financial"
CAT_INFLATION = "inflation"
CAT_LABOUR = "labour"
CAT_GROWTH = "growth"

# Known non-economic headings — matched EXACTLY on the cleaned heading (no
# substring routing), plus the explicit title-style prefix families below. A
# heading that merely contains such a phrase ("External monetary policy
# developments", "Statistical annexes", "Copyright notice") is not a known
# non-economic heading: it falls to the unknown default and is still never
# mined (UNKNOWN ≠ ECONOMIC).
_IGNORE_HEADINGS = frozenset({
    "legal notice",
    "statistical annex",
    "copyright",
    "imprint",
    "disclaimer",
    "external monetary policy",
})
# Title-style families: the meeting-account title ("Account of the monetary
# policy meeting of the Governing Council held on 23 July 2026") and the
# "Minutes of …" document-title form ("Minutes of the Governing Council").
# These are matched structurally (prefix on the cleaned heading), never by
# arbitrary substring matching.
_IGNORE_HEADING_PREFIXES = (
    "account of the monetary policy meeting",
    "minutes of",
)
# Exact identity sets: a heading is routed only when, after deterministic
# normalization, it equals a known heading verbatim. Substring coincidence is
# never enough — "Non-economic developments" shares "economic" with
# "Economic analysis" but must route to IGNORE, never to GENERAL.
_POLICY_HEADINGS = frozenset({
    "monetary policy stance",
    "policy considerations",
    "policy conclusions",
    "monetary policy",
    "monetary policy stance and policy considerations",
})
_RISK_HEADINGS = frozenset({"risk assessment", "risks", "risk"})
_INFLATION_HEADINGS = frozenset({"prices and costs", "price developments", "inflation"})
_GROWTH_HEADINGS = frozenset({"real economy", "economic activity", "growth"})
_LABOUR_HEADINGS = frozenset({"labour market", "employment"})
_FINANCIAL_HEADINGS = frozenset({
    "money, credit and financial conditions",
    "financial conditions",
    "monetary and financial",
})
_GENERAL_HEADINGS = frozenset({"economic analysis", "external environment"})

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
    """Route a section by its normalized heading: ``CAT_IGNORE`` or a mined
    category label. Routing is exact identity on the cleaned heading; the label
    does not constrain the per-sentence classification (content-first), it only
    marks the section as mined.

    Known non-economic headings are matched by exact identity
    (``_IGNORE_HEADINGS``) plus the explicit title-style prefix families
    (``_IGNORE_HEADING_PREFIXES``); any other heading — known economic (exact
    identity) or unknown (the default below, ``UNKNOWN ≠ ECONOMIC``) — is
    classified accordingly. There is no general substring matching."""
    t = _clean_heading(heading)
    if not t:
        return CAT_IGNORE
    if t in _IGNORE_HEADINGS or t.startswith(_IGNORE_HEADING_PREFIXES):
        return CAT_IGNORE
    if t in _POLICY_HEADINGS:
        return CAT_POLICY
    if t in _RISK_HEADINGS:
        return CAT_RISK
    if t in _INFLATION_HEADINGS:
        return CAT_INFLATION
    if t in _GROWTH_HEADINGS:
        return CAT_GROWTH
    if t in _LABOUR_HEADINGS:
        return CAT_LABOUR
    if t in _FINANCIAL_HEADINGS:
        return CAT_FINANCIAL
    if t in _GENERAL_HEADINGS:
        return CAT_GENERAL
    return CAT_IGNORE


# ---------------------------------------------------------------------------
# Attribution. The account records the discussion through a small set of
# subject labels; the label the source itself states is preserved verbatim in
# ``identity_qualifier`` (``minutes:{attribution}:{n}``). Precedence: dissent >
# one member > some members > members > council > collective (unmarked).
# ``Fact.speaker`` stays None: the accounts do not reliably label individuals
# and a name is never invented.
# ---------------------------------------------------------------------------
ATTR_DISSENT = "dissent"
ATTR_ONE_MEMBER = "one_member"
ATTR_SOME_MEMBERS = "some_members"
ATTR_MEMBERS = "members"
ATTR_COUNCIL = "council"
ATTR_COLLECTIVE = "collective"

_ATTR_DISSENT = re.compile(r"\b(?:dissented?|dissenting|voted against|dissenting view)\b", re.IGNORECASE)
_ATTR_ONE_MEMBER = re.compile(r"\bone member\b|\ba single member\b", re.IGNORECASE)
_ATTR_SOME_MEMBERS = re.compile(
    r"\bsome members\b|\bseveral members\b|\ba number of members\b|\ba few members\b|"
    r"\bmost members\b|\ba majority of members\b",
    re.IGNORECASE,
)
_ATTR_MEMBERS = re.compile(r"\bother members\b|\bmembers\b", re.IGNORECASE)
_ATTR_COUNCIL = re.compile(r"\bgoverning council\b|\bthe council\b", re.IGNORECASE)


def _attribution(sentence: str) -> str:
    if _ATTR_DISSENT.search(sentence):
        return ATTR_DISSENT
    if _ATTR_ONE_MEMBER.search(sentence):
        return ATTR_ONE_MEMBER
    if _ATTR_SOME_MEMBERS.search(sentence):
        return ATTR_SOME_MEMBERS
    if _ATTR_MEMBERS.search(sentence):
        return ATTR_MEMBERS
    if _ATTR_COUNCIL.search(sentence):
        return ATTR_COUNCIL
    return ATTR_COLLECTIVE


# ---------------------------------------------------------------------------
# Discussion wording — handled faithfully. A sentence that only names the theme
# of the discussion without stating a position is suppressed: "absence of
# content → absence of extraction". Explicit content ("Members noted that …",
# "Members agreed that …") is mined as usual.
# ---------------------------------------------------------------------------
_META_DISCUSSION = re.compile(
    r"\b(?:the\s+)?discussion\s+(?:focused|centred|centered)\s+on\b"
    r"|\b(?:the\s+)?topic\s+of\s+(?:the\s+)?discussion\b"
    r"|\b(?:the\s+)?subject\s+of\s+(?:the\s+)?discussion\b"
    r"|\bdiscuss(?:ed|ing)\s+(?:the\s+)?(?:possibility|implications?|prospects?)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Category anchors (same precedence as Phase 7, content-first). Guidance (G) >
# policy stance (D) > risks (E) > financial conditions (F) > inflation (A) >
# labour market (C) > growth (B).
# ---------------------------------------------------------------------------
CAT_NONE = "none"
CAT_GUIDANCE = "guidance"

_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(?:stand|stands|stood)\s+ready\s+to\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+not\s+hesitate\s+to\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+keep\s+(?:the\s+)?(?:key\s+)?(?:ecb\s+)?interest\s+rates?\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+not\s+pre-?commit\b", re.IGNORECASE),
    re.compile(r"\bmeeting\s+by\s+meeting\b", re.IGNORECASE),
    re.compile(r"\bmeeting\s*-\s*after\s*-\s*meeting\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+be\s+guided\s+by\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+depend\s+on\b", re.IGNORECASE),
    re.compile(r"\bdata-?dependent\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+(?:assess|monitor)\s+(?:the\s+)?(?:incoming\s+)?data\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+decide\b", re.IGNORECASE),
    re.compile(r"\bfuture\s+(?:policy\s+)?decisions\b", re.IGNORECASE),
    re.compile(r"\bpolicy\s+path\b", re.IGNORECASE),
    re.compile(r"\b(?:will|would)\s+continue\s+to\s+(?:monitor|assess|follow)\b", re.IGNORECASE),
    re.compile(r"\b(?:expects?|expected)\s+(?:the\s+)?(?:key\s+)?(?:ecb\s+)?interest\s+rates?\s+to\s+remain\b", re.IGNORECASE),
)

# D — policy stance/conditions/preferences. Compound signal (a stance word AND a
# policy term) so "the growth trajectory" is never mined as policy; plus the
# stance-phrase pattern for unambiguous "accommodative/restrictive/appropriate
# stance" phrasings that carry no separate policy term.
_POLICY_TERM = re.compile(
    r"\b(?:policy|monetary|rate|rates|interest rates?|governing council|council|instruments? within its mandate)\b",
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
    r"\b(?:appropriate|restrictive|accommodative|neutral|loose)\s+stance\b"
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
    re.compile(r"\bcredit\b", re.IGNORECASE),
    re.compile(r"\bbank lending\b", re.IGNORECASE),
    re.compile(r"\blending\b", re.IGNORECASE),
    re.compile(r"\bspreads?\b", re.IGNORECASE),
    re.compile(r"\btransmission\b", re.IGNORECASE),
    re.compile(r"\bmarket rates?\b", re.IGNORECASE),
    re.compile(r"\bborrowing costs?\b", re.IGNORECASE),
    re.compile(r"\bbond markets?\b", re.IGNORECASE),
    re.compile(r"\bfunding\b", re.IGNORECASE),
    re.compile(r"\bfinancial stability\b", re.IGNORECASE),
)

_INFLATION_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\binflation\b", re.IGNORECASE),
    re.compile(r"\bdisinflation\b", re.IGNORECASE),
    re.compile(r"\bhicp\b", re.IGNORECASE),
    re.compile(r"\bcore\b", re.IGNORECASE),
    re.compile(r"\bdeflation\b", re.IGNORECASE),
    re.compile(r"\bprice pressures\b", re.IGNORECASE),
    re.compile(r"\benergy prices\b", re.IGNORECASE),
    re.compile(r"\bfood prices\b", re.IGNORECASE),
)

_LABOUR_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\blabour market\b", re.IGNORECASE),
    re.compile(r"\blabor market\b", re.IGNORECASE),
    re.compile(r"\bunemployment\b", re.IGNORECASE),
    re.compile(r"\bemployment\b", re.IGNORECASE),
    re.compile(r"\bwage\b", re.IGNORECASE),
    re.compile(r"\bwages\b", re.IGNORECASE),
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
    re.compile(r"\brecession\b", re.IGNORECASE),
    re.compile(r"\bslowdown\b", re.IGNORECASE),
    re.compile(r"\bexpansion\b", re.IGNORECASE),
)

# ---------------------------------------------------------------------------
# Quantitative values — same value gate as Phases 6/7: only explicit value
# claims ("projected / expected … to average / stand at …", "stood at …").
# ---------------------------------------------------------------------------
_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"
_MONTH_NUM = {
    "january": "01", "february": "02", "march": "03", "april": "04", "may": "05", "june": "06",
    "july": "07", "august": "08", "september": "09", "october": "10", "november": "11", "december": "12",
}
_VALUE_TOKEN = rf"{_RATE_ITEM}\s*(?:%|per\s+cent)"

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
    """Split paragraph text into non-empty sentences, each verbatim (trailing
    period preserved). Both ``". "`` and ``".\\n"`` are boundaries."""
    return [part.strip() for part in re.split(r"(?<=\.)\s+", text or "") if part.strip()]


class _RunState:
    """Mutable run state threaded through the sentence miners."""

    __slots__ = ("risk_found", "guidance_found")

    def __init__(self) -> None:
        self.risk_found = False
        self.guidance_found = False


class EcbMinutesExtractor(MinutesExtractor):
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

    # ------------------------------------------------------------------
    # section walking — every recognized section is mined content-first
    # ------------------------------------------------------------------
    def _process_section(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        text: str,
        counters: dict[str, int],
        state: _RunState,
    ) -> None:
        for sentence in _split_sentences(text):
            self._mine_sentence(result, document, index, sentence, counters, state)

    # ------------------------------------------------------------------
    # sentence classification (categories A–G) and fact emission
    # ------------------------------------------------------------------
    def _mine_sentence(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        counters: dict[str, int],
        state: _RunState,
    ) -> None:
        if _META_DISCUSSION.search(sentence):
            return  # theme-only discussion wording: no position, no fact
        category = self._categorize(sentence)
        attribution = _attribution(sentence)
        context = f"minutes:{attribution}"
        if category == CAT_GUIDANCE:
            state.guidance_found = True
            result.add(
                self._text_fact(result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, PREDICATE_STATEMENT, counters, context)
            )
        elif category == CAT_POLICY:
            result.add(
                self._text_fact(result, document, index, sentence, SUBJECT_MONETARY_POLICY, PREDICATE_STATEMENT, counters, context)
            )
        elif category == CAT_RISK:
            state.risk_found = True
            self._add_risk_facts(result, document, index, sentence, counters, context)
        elif category == CAT_FINANCIAL:
            if not self._add_value_facts(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, counters, context):
                result.add(
                    self._text_fact(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, PREDICATE_ASSESSMENT, counters, context)
                )
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
    # risk assessment
    # ------------------------------------------------------------------
    @classmethod
    def _add_risk_facts(cls, result, document, index: int, sentence: str, counters: dict, context: str) -> None:
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
                    Confidence.HIGH, counters, context=context,
                )
            )
        else:
            result.add(cls._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters, context))

    # ------------------------------------------------------------------
    # quantitative value claims
    # ------------------------------------------------------------------
    @classmethod
    def _add_value_facts(cls, result, document, index: int, sentence: str, subject: str, counters: dict, context: str) -> int:
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
                    Confidence.HIGH, counters, period=period, context=context,
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
    # inflation / growth / labour market
    # ------------------------------------------------------------------
    @classmethod
    def _add_inflation_facts(cls, result, document, index: int, sentence: str, counters: dict, context: str) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        elif "core inflation" in lower or "core hicp" in lower:
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
