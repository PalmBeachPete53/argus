"""ECB — Speech / Remarks / Address extractor (Phase 4.7).

Extracts the facts of an ECB speech from the normalized document, answering
"what does this official explicitly state about the economy, its risks and
about monetary policy?":

- the **economic outlook** — inflation (``inflation`` / ``core_inflation`` /
  ``inflation_expectations``), growth (``growth`` qualitative, ``gdp``
  quantitative), the labour market (``labour_market`` / ``unemployment`` /
  ``wages``) and financial conditions, as verbatim assessments and as explicit
  quantitative value claims;
- the **risk assessment**, as categorical orientations (``upside`` /
  ``downside`` / ``balanced``) only when explicitly stated, otherwise verbatim
  text;
- explicit **policy statements** and **forward guidance**, verbatim, never
  interpreted.

A speech is the **individual** communication of one official. Its cardinal rule
is **precision over recall** — ``explicit assertion + identifiable subject +
predicate + value/state + period (when required) + provenance → Fact``, and
everything else is IGNORED:

- **Speaker**: preserved verbatim in ``Fact.speaker`` only when the source
  states one — a ``Speaker: <label>`` line in the document body or an explicit
  author field in the document metadata. Never inferred: "the President", "the
  ECB" or "the Governing Council" are never turned into a person, and a name in
  the text ("Christine Lagarde was born in Paris") is never read as the speaker.
  A quoted sentence attributed to someone else is never attributed to the
  speech's speaker (``quoted_content_skipped``).
- **Metadata isolation**: the title, subtitle, event name, conference name,
  location and author metadata never create facts — they only ever feed the
  provenance attribution above.
- **Routing**: a known economic heading is mined in full (content-first
  sentence classification); a **known non-economic heading** (biography, thanks,
  closing remarks, Q&A, legal/back matter) is ignored; an **unknown heading** is
  mined at paragraph level but **never yields an automatic fact** — only
  explicit assertions pass (a quantitative value claim, a categorical risk
  orientation, a guidance or a policy sentence). Personal anecdotes, biography,
  ceremonial thanks, history without explicit values and quoted authors are
  never facts.

Deliberately NOT extracted (Phase 4.7 boundary):

- hawkish/dovish, bullish/bearish or any market interpretation — never
- policy decisions / rates / changes / votes — Phase 4.1 and 8, gated on their
  own publication types; a speech's *narrative* of policy is kept verbatim,
  never priced
- the Q&A of a speech document (journalist content; press-conference Q&A is
  Phase 4.3), fiscal analysis (Phase 4.6), structured projections tables
  (Phase 4.5/10)
- an individual statement is never a collective decision: no Phase 4.1–4.6
  subject is ever emitted here.

Design rules

- No fact is invented. A value/orientation is only produced when the source
  states it, and every Fact preserves an *exact verbatim* supporting passage
  (``source_text``) copied from the normalized document.
- **Units are explicit.** Values are kept as ``percentage`` only when the
  sentence states a percentage; share/ratio units are never converted, and a
  forecast value claim without an explicit reference period is
  under-determined and ignored.
- **Periods** come from the sentence wording (year, month, quarter) — never
  guessed from proximity. ``Fact.effective_date`` is always ``None``.
- Risk facts are categorical (``upside`` / ``downside`` / ``balanced``) only
  when the source states an explicit orientation; otherwise they are verbatim
  text assessments.
- The same assertion stated twice is emitted **once** (exact-verbatim / exact
  value+period duplicates are suppressed within a run).
- Confidence is ``HIGH`` for quantitative percentages and categorical risk
  orientations (explicit source wording); ``MEDIUM`` for verbatim qualitative
  assessments.
"""

from __future__ import annotations

import re

from ..documents.base import NormalizedDocument
from ..facts import ExtractionResult
from ._shared import (
    PREDICATE_ASSESSMENT,
    PREDICATE_STATEMENT,
    SUBJECT_CORE_INFLATION,
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
    is_economic_assertion,
    is_quoted_other,
    speaker_from_document,
    split_sentences,
)
from .base import SpeechExtractor

EXTRACTION_VERSION = "11.0.0"

# ---------------------------------------------------------------------------
# Section routing — CONSERVATIVE (Phase 4.7). A heading is mined in full only
# when it is a known economic section; a known non-economic heading (biography,
# thanks, closing remarks, Q&A, legal/back matter) is ignored; an **unknown
# heading** is mined at paragraph level but never yields an automatic fact —
# only explicit assertions pass. "Absence of proof → absence of extraction".
# ---------------------------------------------------------------------------
CAT_IGNORE = "ignore"
CAT_UNKNOWN = "unknown"
CAT_ECONOMIC = "economic"

# Known non-economic headings are matched EXACTLY on the normalized heading:
# substring coincidence never determines identity. Every supported variant is
# listed explicitly.
_IGNORE_HEADINGS = frozenset({
    # speech masthead / type labels
    "speech", "speech by", "remarks", "address", "keynote speech", "keynote address",
    # front matter
    "about the speaker", "speaker biography", "biography", "biographical note",
    "acknowledgements", "acknowledgments", "thanks", "thank you",
    "closing remarks", "concluding remarks", "closing",
    # Q&A — journalist content (press-conference Q&A is Phase 4.3)
    "questions and answers", "questions", "question", "answers", "q&a", "questions from",
    # legal & back matter
    "references", "bibliography", "further reading", "notes", "endnotes",
    "annex", "appendix", "legal notice", "disclaimer", "copyright", "imprint",
    "glossary",
})

# Known economic headings (exact normalized strings). The heading only gates
# *whether* the section is mined in full; content classification is
# content-first, so compound headings ("Inflation and monetary policy") that
# are not a controlled exact label fall to UNKNOWN and are strictly mined.
_ECONOMIC_HEADINGS = frozenset({
    # policy
    "monetary policy", "monetary policy stance", "monetary policy transmission",
    "policy considerations", "policy stance",
    # risk
    "risk assessment", "risks", "risk",
    # inflation
    "inflation", "prices and costs", "price developments", "price stability",
    # labour
    "labour market", "labor market", "employment", "wages", "wage developments",
    # financial
    "financial stability", "financial conditions", "financial developments",
    "financial markets", "money and credit", "monetary and financial",
    "financial system",
    # growth
    "economic outlook", "economic activity", "real economy", "growth",
    "economic growth", "output", "euro area economy",
    # general
    "external environment", "international environment",
    "economic and monetary developments", "economic analysis", "overview",
    "summary", "executive summary", "world economy", "global economy", "economic",
})

def _section_category(heading: str) -> str:
    """Route a section by its normalized heading.

    Returns ``CAT_IGNORE`` (a known non-economic controlled heading or a
    heading-less section / analytical box), ``CAT_ECONOMIC`` (a known economic
    heading, mined in full), or ``CAT_UNKNOWN`` (a heading that is neither —
    strictly mined, explicit assertions only). Exact membership only; substring
    coincidence never determines identity. Heading normalization is the shared
    structural `clean_heading`; the heading *vocabulary* is ECB-specific.
    """
    t = clean_heading(heading or "")
    if not t or is_box(t):
        return CAT_IGNORE  # heading-less sections and analytical boxes are never mined
    if t in _IGNORE_HEADINGS:
        return CAT_IGNORE
    if t in _ECONOMIC_HEADINGS:
        return CAT_ECONOMIC
    return CAT_UNKNOWN


# ---------------------------------------------------------------------------
# Category anchors, content-first. Fixed precedence: guidance (G) > policy


# ---------------------------------------------------------------------------
# Category anchors, content-first. Fixed precedence: guidance (G) > policy
# stance (D) > risks (E) > financial conditions (F) > inflation (A) > labour
# market (C) > growth (B). A sentence matching none is ignored.
# ---------------------------------------------------------------------------
CAT_NONE = "none"
CAT_GUIDANCE = "guidance"
CAT_POLICY = "policy"
CAT_RISK = "risk"
CAT_FINANCIAL = "financial"
CAT_INFLATION = "inflation"
CAT_LABOUR = "labour"
CAT_GROWTH = "growth"

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
# single words are not a policy signal.
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
    # "credit" alone is too generic ("Credit is important.") — it fires only as
    # a contextual credit-conditions marker. Phase 4.7 hardening.
    re.compile(r"\bcredit\s+(?:growth|standards|supply|demand|conditions?|availability|creation|extension|provision|restrictions?|tightening|easing|expansion|flows?)\b", re.IGNORECASE),
    re.compile(r"\bbank lending\b", re.IGNORECASE),
    re.compile(r"\b(?:bank\s+lending|lending\s+(?:rates?|growth|to|conditions?|standards?))\b", re.IGNORECASE),
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
# and must never anchor (or emit) a GDP value fact on their own (same guard as
# Phase 4.6 reports).
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
    # bare "output" is a growth marker ("output increased"); the gate still
    # requires an explicit assertion for a qualitative fact. Phase 4.7 hardening.
    re.compile(r"\boutput\b", re.IGNORECASE),
    # "demand" alone is too generic — only a qualified demand is a growth signal
    re.compile(r"\b(?:domestic|aggregate|global|external|private|overall|total)\s+demand\b", re.IGNORECASE),
    re.compile(r"\bconsumption\b", re.IGNORECASE),
    re.compile(r"\binvestment\b", re.IGNORECASE),
    # "production" alone is too generic — only sector-specific production is a
    # growth signal
    re.compile(r"\b(?:industrial|manufacturing|energy|oil|steel|automotive)\s+production\b", re.IGNORECASE),
)


class _RunState:
    """Mutable run state threaded through the sentence miners."""

    __slots__ = ("risk_found", "guidance_found", "quoted_skipped")

    def __init__(self) -> None:
        self.risk_found = False
        self.guidance_found = False
        self.quoted_skipped = False


class EcbSpeechExtractor(SpeechExtractor):
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

        speaker = speaker_from_document(document)
        reporter = Reporter(extraction_version=EXTRACTION_VERSION)
        state = _RunState()

        for index, section in enumerate(document.sections):
            category = _section_category(section.heading or "")
            if category == CAT_IGNORE:
                continue
            strict = category == CAT_UNKNOWN
            self._process_section(result, document, index, section.text or "", reporter, state, speaker=speaker, strict=strict)

        if not state.risk_found:
            result.warnings.append("no_risk_assessment")
        if not state.guidance_found:
            result.warnings.append("no_forward_guidance")
        if state.quoted_skipped:
            result.warnings.append("quoted_content_skipped")
        return result

    # ------------------------------------------------------------------
    # section walking — known economic sections are mined in full, unknown
    # sections are strictly mined (explicit assertions only)
    # ------------------------------------------------------------------
    def _process_section(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        text: str,
        reporter: Reporter,
        state: _RunState,
        *,
        speaker: str | None,
        strict: bool,
    ) -> None:
        for sentence in split_sentences(text):
            self._mine_sentence(result, document, index, sentence, reporter, state, speaker=speaker, strict=strict)

    # ------------------------------------------------------------------
    # sentence classification (guidance > policy > risk > financial >
    # inflation > labour > growth) and fact emission
    # ------------------------------------------------------------------
    def _mine_sentence(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        reporter: Reporter,
        state: _RunState,
        *,
        speaker: str | None,
        strict: bool,
    ) -> None:
        if is_quoted_other(sentence, speaker):
            state.quoted_skipped = True
            return
        category = self._categorize(sentence)
        if category == CAT_GUIDANCE:
            state.guidance_found = True
            reporter.emit_text(result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, PREDICATE_STATEMENT, speaker)
        elif category == CAT_POLICY:
            reporter.emit_text(result, document, index, sentence, SUBJECT_MONETARY_POLICY, PREDICATE_STATEMENT, speaker)
        elif category == CAT_RISK:
            state.risk_found = True
            self._add_risk_facts(result, document, index, sentence, reporter, speaker, strict=strict)
        elif category == CAT_FINANCIAL:
            if not reporter.emit_value_facts(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, speaker):
                if not strict and is_economic_assertion(sentence):
                    reporter.emit_text(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, PREDICATE_ASSESSMENT, speaker)
        elif category == CAT_INFLATION:
            self._add_inflation_facts(result, document, index, sentence, reporter, speaker, strict=strict)
        elif category == CAT_LABOUR:
            self._add_labour_facts(result, document, index, sentence, reporter, speaker, strict=strict)
        elif category == CAT_GROWTH:
            self._add_growth_facts(result, document, index, sentence, reporter, speaker, strict=strict)

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

    # ------------------------------------------------------------------
    # risk assessment
    # ------------------------------------------------------------------
    @staticmethod
    def _add_risk_facts(result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, reporter: Reporter, speaker: str | None, *, strict: bool) -> None:
        """A risk sentence yields a categorical orientation fact when an
        explicit orientation word is present; otherwise a verbatim text
        assessment (unknown sections keep only the categorical orientation —
        a bare risk mention is never an automatic fact)."""
        lower = sentence.lower()
        if "inflation" in lower:
            subject = SUBJECT_INFLATION_RISK
        elif "growth" in lower or "activity" in lower or "gdp" in lower:
            subject = SUBJECT_GROWTH_RISK
        else:
            subject = SUBJECT_RISK
        reporter.emit_risk(result, document, index, sentence, subject, speaker, strict=strict)

    # ------------------------------------------------------------------
    # inflation / growth / labour market (subject resolution is bank-specific;
    # emission and the value/assertion gates are the shared structural helpers)
    # ------------------------------------------------------------------
    @staticmethod
    def _add_inflation_facts(result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, reporter: Reporter, speaker: str | None, *, strict: bool) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        elif "core inflation" in lower or "core hicp" in lower:
            subject = SUBJECT_CORE_INFLATION
        else:
            subject = SUBJECT_INFLATION
        if reporter.emit_value_facts(result, document, index, sentence, subject, speaker):
            return
        if not strict and is_economic_assertion(sentence):
            reporter.emit_text(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, speaker)

    @staticmethod
    def _add_growth_facts(result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, reporter: Reporter, speaker: str | None, *, strict: bool) -> None:
        # A GDP deflator / per-capita mention inside an otherwise-growth
        # sentence must not leak into a GDP value fact (precision first).
        if _GDP_NEAR_MISS.search(sentence):
            return
        if reporter.emit_value_facts(result, document, index, sentence, SUBJECT_GDP, speaker):
            return
        if not strict and is_economic_assertion(sentence):
            reporter.emit_text(result, document, index, sentence, SUBJECT_GROWTH, PREDICATE_ASSESSMENT, speaker)

    @staticmethod
    def _add_labour_facts(result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, reporter: Reporter, speaker: str | None, *, strict: bool) -> None:
        lower = sentence.lower()
        if "unemployment" in lower:
            subject = SUBJECT_UNEMPLOYMENT
        elif "wage" in lower:
            subject = SUBJECT_WAGES
        else:
            subject = SUBJECT_LABOUR_MARKET
        if reporter.emit_value_facts(result, document, index, sentence, subject, speaker):
            return
        if not strict and is_economic_assertion(sentence):
            reporter.emit_text(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, speaker)
