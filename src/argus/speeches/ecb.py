"""ECB — Speech / Remarks / Address extractor (Phase 11).

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

Deliberately NOT extracted (Phase 11 boundary):

- hawkish/dovish, bullish/bearish or any market interpretation — never
- policy decisions / rates / changes / votes — Phases 5 and 8, gated on their
  own publication types; a speech's *narrative* of policy is kept verbatim,
  never priced
- the Q&A of a speech document (journalist content; press-conference Q&A is
  Phase 7), fiscal analysis (Phase 10), structured projections tables
  (Phases 9/10)
- an individual statement is never a collective decision: no Phase 5–10
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
from .base import SpeechExtractor

EXTRACTION_VERSION = "11.0.0"

# ---------------------------------------------------------------------------
# Canonical Phase 11 subjects (controlled vocabulary, see docs/EXTRACTORS.md).
# Reuses the Phase 6/7/8/10 subjects verbatim; no subject is added in Phase 11
# (speeches reuse the shared economic vocabulary, and fiscal analysis — Phase
# 10 — is deliberately out of scope).
# ---------------------------------------------------------------------------
SUBJECT_INFLATION = "inflation"
SUBJECT_CORE_INFLATION = "core_inflation"
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

PREDICATE_ASSESSMENT = "assessment"
PREDICATE_STATEMENT = "statement"
PREDICATE_VALUE = "value"

# ---------------------------------------------------------------------------
# Section routing — CONSERVATIVE (Phase 11). A heading is mined in full only
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
    # Q&A — journalist content (press-conference Q&A is Phase 7)
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

_FOOTNOTE_MARK = re.compile(r"\s*(?:\(\d+\)|\[\d+\]|\d+\)|[*†‡]+)\s*$")
_BOX_PREFIX = re.compile(r"^\s*box\b", re.IGNORECASE)
_LEADING_NUM = re.compile(r"^\s*(?:[0-9]+(?:\.[0-9]+)*)\s*[-–—.:]?\s*")
_LEADING_THE = re.compile(r"^the\s+")
_TRAILING_PUNCT = re.compile(r"[\s.:;,\-–—]+$")


def _section_category(heading: str) -> str:
    """Route a section by its normalized heading.

    Returns ``CAT_IGNORE`` (a known non-economic controlled heading or a
    heading-less section / analytical box), ``CAT_ECONOMIC`` (a known economic
    heading, mined in full), or ``CAT_UNKNOWN`` (a heading that is neither —
    strictly mined, explicit assertions only). Exact membership only; substring
    coincidence never determines identity.
    """
    t = normalize_title(heading or "")
    if not t:
        return CAT_IGNORE
    if _BOX_PREFIX.match(t):
        return CAT_IGNORE  # analytical boxes are never mined
    t = _LEADING_NUM.sub("", t).strip()
    t = _FOOTNOTE_MARK.sub("", t).strip()
    t = _LEADING_THE.sub("", t).strip()
    t = _TRAILING_PUNCT.sub("", t).strip()
    if t in _IGNORE_HEADINGS:
        return CAT_IGNORE
    if t in _ECONOMIC_HEADINGS:
        return CAT_ECONOMIC
    return CAT_UNKNOWN


# ---------------------------------------------------------------------------
# Speaker attribution — explicit only. A ``Speaker: <label>`` line in the body
# wins over an explicit author field in the document metadata; when neither is
# present the speaker is ``None`` (never inferred). The label is preserved
# verbatim in ``Fact.speaker``.
# ---------------------------------------------------------------------------
_SPEAKER_LINE = re.compile(r"^\s*speaker\s*[:\-–]\s*(?P<speaker>.+?)\s*$", re.IGNORECASE)

_META_AUTHOR_KEYS = ("author", "dc.creator")


def _speaker_from_document(document: NormalizedDocument) -> str | None:
    for section in document.sections:
        for line in (section.text or "").split("\n"):
            match = _SPEAKER_LINE.match(line)
            if match is not None:
                label = match.group("speaker").strip().rstrip(".:")
                if label:
                    return label
    meta = (document.metadata or {}).get("html_meta", {})
    for key in _META_AUTHOR_KEYS:
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# ---------------------------------------------------------------------------
# Quoted content — a sentence framed as a quotation of a person other than the
# speech's own speaker is never mined (never attributed to the speaker).
# ---------------------------------------------------------------------------
_NAME = r"[A-ZÀ-Þ][A-Za-zÀ-ÿ]+(?:\s+[A-ZÀ-Þ][A-Za-zÀ-ÿ]+)*"
_QUOTE_VERBS = r"said|wrote|noted|observed|argued|stated|quoted|put\s+it|pointed\s+out"

# A person's name is matched case-sensitively (a name never swallows lowercase
# auxiliary or verb words, even under case-insensitive verb matching).
_QUOTE_AS = re.compile(rf"\b(?i:as)\s+(?P<name>{_NAME})\s+(?i:(?:has\s+)?(?:once\s+)?(?:{_QUOTE_VERBS}))\b")
_QUOTE_ACCORDING = re.compile(rf"\b(?i:according)\s+to\s+(?P<name>{_NAME})\b")
_QUOTE_VERB_THAT = re.compile(rf"\b(?P<name>{_NAME})\s+(?i:(?:once\s+)?(?:{_QUOTE_VERBS}))\s+that\b")
_QUOTE_QUOTING = re.compile(rf"\b(?i:(?:quoting|quoted))\s+(?P<name>{_NAME})\b")
_QUOTE_PATTERNS = (_QUOTE_AS, _QUOTE_ACCORDING, _QUOTE_VERB_THAT, _QUOTE_QUOTING)

_SELF_REFERENCE = {"i", "we"}


def _is_quoted_other(sentence: str, speaker_label: str | None) -> bool:
    """True when ``sentence`` is framed as a quotation of a person other than
    the speech's speaker. The speaker quoting their own past words ("As I
    said …", "As Christine Lagarde said …") is never treated as a quotation of
    another person."""
    for pattern in _QUOTE_PATTERNS:
        for match in pattern.finditer(sentence):
            name = match.group("name").strip().lower()
            if name in _SELF_REFERENCE:
                continue
            if speaker_label and name in speaker_label.lower():
                continue
            return True
    return False


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
    # a contextual credit-conditions marker. Phase 11 hardening.
    re.compile(r"\bcredit\s+(?:growth|standards|supply|demand|conditions?|availability|creation|extension|provision|restrictions?|tightening|easing|expansion|flows?)\b", re.IGNORECASE),
    re.compile(r"\bbank lending\b", re.IGNORECASE),
    re.compile(r"\b(?:bank\s+lending|lending\s+(?:rates?|growth|to|conditions?))\b", re.IGNORECASE),
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

_GROWTH_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bgrowth\b", re.IGNORECASE),
    re.compile(r"\bgdp\b", re.IGNORECASE),
    re.compile(r"\b(?:economic\s+)?activity\b", re.IGNORECASE),
    re.compile(r"\beconom(?:y|ic)\b", re.IGNORECASE),
    # bare "output" is a growth marker ("output increased"); the gate still
    # requires an explicit assertion for a qualitative fact. Phase 11 hardening.
    re.compile(r"\boutput\b", re.IGNORECASE),
    # "demand" alone is too generic — only a qualified demand is a growth signal
    re.compile(r"\b(?:domestic|aggregate|global|external|private|overall|total)\s+demand\b", re.IGNORECASE),
    re.compile(r"\bconsumption\b", re.IGNORECASE),
    re.compile(r"\binvestment\b", re.IGNORECASE),
    # "production" alone is too generic — only sector-specific production is a
    # growth signal
    re.compile(r"\b(?:industrial|manufacturing|energy|oil|steel|automotive)\s+production\b", re.IGNORECASE),
)

# ---------------------------------------------------------------------------
# Qualitative fact gate — Phase 11 hardening. A qualitative assessment is only
# emitted when the sentence states an explicit economic assertion. Economic
# vocabulary alone ("the economy", "credit", "investment", "growth") is never
# enough: an assertion signal (a change/state verb, a "remains/continues" form,
# or an expected/projected-to forecast) must be present, and platitude rhetoric
# ("X is important", "X matters", "X is a priority") is always rejected.
# ---------------------------------------------------------------------------
_ASSERTION_SIGNAL = re.compile(
    r"\b(?:"
    r"increas(?:e|es|ed|ing)"
    r"|decreas(?:e|es|ed|ing)"
    r"|declin(?:e|es|ed|ing)"
    r"|ris(?:e|es|ing)|rose|risen"
    r"|fall(?:s|ing|en)?|fell|fallen"
    r"|grow(?:s|ing)?|grew|grown"
    r"|strengthen(?:s|ed|ing)?"
    r"|weaken(?:s|ed|ing)?"
    r"|accelerat(?:e|es|ed|ing)"
    r"|decelerat(?:e|es|ed|ing)"
    r"|moderat(?:e|es|ed|ing)"
    r"|improv(?:e|es|ed|ing)"
    r"|deteriorat(?:e|es|ed|ing)"
    r"|expand(?:s|ed|ing)?"
    r"|contract(?:s|ed|ing)?"
    r"|recover(?:s|ed|ing)?"
    r"|rebound(?:s|ed|ing)?"
    r"|slow(?:s|ed|ing)?"
    r"|ease(?:s|d|ing)?"
    r"|tighten(?:s|ed|ing)?"
    r"|loosen(?:s|ed|ing)?"
    r"|narrow(?:s|ed|ing)?"
    r"|widen(?:s|ed|ing)?"
    r"|surge(?:s|d|ing)?"
    r"|dropped|drop(?:s|ping)?"
    r"|gain(?:s|ed|ing)?"
    r"|lost|lose(?:s|ing)?"
    r"|normalis(?:e|es|ed|ing)|normaliz(?:e|es|ed|ing)"
    r"|broaden(?:s|ed|ing)?"
    r"|pick(?:s|ed)?\s+up"
    r"|remain(?:s|ed|ing)?|stay(?:s|ed|ing)?|continue(?:s|d|ing)?"
    r"|(?:is|are|was|were|will|would|has|have)\s+(?:expected|projected|estimated|forecast|likely)\s+to"
    r"|will\s+(?:remain|continue)"
    r")\b",
    re.IGNORECASE,
)

_PLATITUDE = re.compile(
    r"\b(?:"
    r"(?:is|are|was|were|be|been|being|remains?|remained|stays?|stayed)\s+"
    r"(?:important|essential|central|critical|crucial|vital|fundamental|necessary|"
    r"a\s+(?:(?:key|crucial|central|important|vital|essential|strategic)\s+)?(?:priority|challenge|matter|goal|mandate)|"
    r"part\s+of\s+life|"
    r"at\s+the\s+(?:heart|centre|center)\s+of)"
    r"|matter(?:s|ed)?"
    r"|(?:is|are|was|were)\s+(?:our|the)\s+(?:priority|goal|aim|mandate|responsibility|focus|task)"
    r")\b",
    re.IGNORECASE,
)


def _is_economic_assertion(sentence: str) -> bool:
    """True when the sentence makes an explicit economic assertion rather than
    merely mentioning a topic. Phase 11 hardening: economic vocabulary alone is
    insufficient."""
    return bool(_ASSERTION_SIGNAL.search(sentence)) and not bool(_PLATITUDE.search(sentence))

# ---------------------------------------------------------------------------
# Quantitative values — explicit value claims only. A percentage is only mined
# when the sentence states a value claim; share/ratio units are never converted
# into percentage facts, and a forecast value without an explicit reference
# period is ignored.
# ---------------------------------------------------------------------------
_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"
_MONTH_NUM = {
    "january": "01", "february": "02", "march": "03", "april": "04", "may": "05", "june": "06",
    "july": "07", "august": "08", "september": "09", "october": "10", "november": "11", "december": "12",
}
_QUARTER_NUM = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}
_VALUE_TOKEN = rf"{_RATE_ITEM}\s*(?:%|per\s+cent|percent)"

_VALUES: tuple[tuple[str, ...], ...] = (
    ("average", "stand at", "be", "reach", "amount to", "grow by", "grow from", "expand by", "expand from",
     "increase by", "increase from", "increase to", "rise by", "rise from", "rise to",
     "decline to", "decline from", "fall to", "fall from", "remain at"),
    ("stood at", "standing at", "averaged", "was at", "were at", "is at", "are at", "stands at", "running at",
     "remain(s|ed|ing)? at", "declined to", "declined by", "declined from", "fell to", "fell by", "fell from",
     "dropped to", "dropped by", "dropped from", "rose to", "rose by", "rose from",
     "increased to", "increased by", "increased from", "expanded by", "grew by", "contracted by",
     "narrowed to", "widened to", "reached"),
)
_VALUE_GATE = re.compile(
    r"(?:projected|expected|forecast)\s+(?:to\s+)?(?:"
    + "|".join(_VALUES[0])
    + r")\s+"
    r"|(?:"
    + "|".join(_VALUES[1])
    + r")\s+",
    re.IGNORECASE,
)
_FORECAST_VERB = re.compile(r"\b(?:projected|expected|forecast)\b", re.IGNORECASE)
_PERCENT_WITH_QUARTER = re.compile(
    rf"(?P<token>{_VALUE_TOKEN})\s+(?P<period>in|during)\s+(?:the\s+)?(?P<quarter>first|second|third|fourth)\s+quarter\s+of\s+(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_PERCENT_WITH_MONTH = re.compile(
    rf"(?P<token>{_VALUE_TOKEN})\s+(?P<period>in|during)\s+(?P<month>{_MONTH_WORDS})\s+(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_PERCENT_WITH_YEAR = re.compile(
    rf"(?P<token>{_VALUE_TOKEN})\s+(?P<period>in|during|by|for)\s+(?:the\s+)?(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_VALUE_TOKEN_ONLY = re.compile(rf"(?P<token>{_VALUE_TOKEN})", re.IGNORECASE)
_SHARE_UNIT = re.compile(
    r"\bof\s+(?:gdp|gross\s+domestic\s+product|total|disposable\s+income)\b", re.IGNORECASE
)


def _split_sentences(text: str) -> list[str]:
    """Split paragraph text into non-empty sentences, each verbatim (trailing
    period preserved). Both ``". "`` and ``".\\n"`` are boundaries."""
    return [part.strip() for part in re.split(r"(?<=\.)\s+", text or "") if part.strip()]


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

        speaker = _speaker_from_document(document)
        counters: dict[tuple, int] = {}
        seen: set[tuple] = set()
        state = _RunState()

        for index, section in enumerate(document.sections):
            category = _section_category(section.heading or "")
            if category == CAT_IGNORE:
                continue
            strict = category == CAT_UNKNOWN
            self._process_section(result, document, index, section.text or "", counters, seen, state, speaker=speaker, strict=strict)

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
        counters: dict,
        seen: set,
        state: _RunState,
        *,
        speaker: str | None,
        strict: bool,
    ) -> None:
        for sentence in _split_sentences(text):
            self._mine_sentence(result, document, index, sentence, counters, seen, state, speaker=speaker, strict=strict)

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
        counters: dict,
        seen: set,
        state: _RunState,
        *,
        speaker: str | None,
        strict: bool,
    ) -> None:
        if _is_quoted_other(sentence, speaker):
            state.quoted_skipped = True
            return
        category = self._categorize(sentence)
        if category == CAT_GUIDANCE:
            state.guidance_found = True
            self._emit_text(result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, PREDICATE_STATEMENT, counters, seen, speaker)
        elif category == CAT_POLICY:
            self._emit_text(result, document, index, sentence, SUBJECT_MONETARY_POLICY, PREDICATE_STATEMENT, counters, seen, speaker)
        elif category == CAT_RISK:
            state.risk_found = True
            self._add_risk_facts(result, document, index, sentence, counters, seen, speaker, strict=strict)
        elif category == CAT_FINANCIAL:
            if not self._add_value_facts(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, counters, seen, speaker):
                if not strict and _is_economic_assertion(sentence):
                    self._emit_text(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, PREDICATE_ASSESSMENT, counters, seen, speaker)
        elif category == CAT_INFLATION:
            self._add_inflation_facts(result, document, index, sentence, counters, seen, speaker, strict=strict)
        elif category == CAT_LABOUR:
            self._add_labour_facts(result, document, index, sentence, counters, seen, speaker, strict=strict)
        elif category == CAT_GROWTH:
            self._add_growth_facts(result, document, index, sentence, counters, seen, speaker, strict=strict)

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
    # emission with within-run deduplication
    # ------------------------------------------------------------------
    @classmethod
    def _emit(
        cls,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        source_text: str,
        subject: str,
        predicate: str,
        value: FactValue,
        confidence: Confidence,
        counters: dict,
        seen: set,
        *,
        period: FactPeriod | None = None,
        speaker: str | None = None,
    ) -> Fact | None:
        """Build a Fact with a deterministic ordinal qualifier, suppressing
        within-run duplicates. A quantitative duplicate is defined by subject +
        predicate + period + value; a qualitative one by subject + predicate +
        period + normalized verbatim wording."""
        period_key = period.canonical() if period else ""
        if value.kind in (ValueKind.NUMBER, ValueKind.PERCENTAGE, ValueKind.BASIS_POINTS, ValueKind.CURRENCY):
            dedup_key = (subject, predicate, period_key, value.value)
        else:
            dedup_key = (subject, predicate, period_key, normalize_title(source_text or ""))
        if dedup_key in seen:
            return None
        seen.add(dedup_key)

        key = (subject, predicate, period_key)
        ordinal = counters.get(key, 0)
        counters[key] = ordinal + 1

        return Fact(
            publication_id=result.publication_id,
            document_id=document.document_id,
            subject=subject,
            predicate=predicate,
            value=value,
            period=period,
            effective_date=None,
            source_location=FactLocation(LocationKind.SECTION, section=index),
            source_text=source_text,
            extraction_method=METHOD_REGEX,
            extraction_version=EXTRACTION_VERSION,
            confidence=confidence,
            speaker=speaker,
            identity_qualifier=f"speech:{subject}:{ordinal}",
        )

    @classmethod
    def _emit_text(
        cls,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        subject: str,
        predicate: str,
        counters: dict,
        seen: set,
        speaker: str | None,
    ) -> None:
        fact = cls._emit(
            result, document, index, sentence, subject, predicate,
            FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
            Confidence.MEDIUM, counters, seen, speaker=speaker,
        )
        if fact is not None:
            result.add(fact)

    # ------------------------------------------------------------------
    # risk assessment
    # ------------------------------------------------------------------
    @classmethod
    def _add_risk_facts(cls, result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, counters: dict, seen: set, speaker: str | None, *, strict: bool) -> None:
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

        orientation = None
        if re.search(r"\b(?:broadly\s+)?balanced\b|\btwo-sided\b|\bsymmetric\b", lower):
            orientation = "balanced"
        elif re.search(r"\bdownside\b", lower):
            orientation = "downside"
        elif re.search(r"\bupside\b", lower):
            orientation = "upside"

        if orientation is not None:
            fact = cls._emit(
                result, document, index, sentence, subject, PREDICATE_ASSESSMENT,
                categorical(orientation, source_text=sentence),
                Confidence.HIGH, counters, seen, speaker=speaker,
            )
        elif not strict and _is_economic_assertion(sentence):
            fact = cls._emit(
                result, document, index, sentence, subject, PREDICATE_ASSESSMENT,
                FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
                Confidence.MEDIUM, counters, seen, speaker=speaker,
            )
        else:
            fact = None
        if fact is not None:
            result.add(fact)

    # ------------------------------------------------------------------
    # quantitative value claims
    # ------------------------------------------------------------------
    @classmethod
    def _add_value_facts(cls, result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, subject: str, counters: dict, seen: set, speaker: str | None) -> int:
        """Emit ``subject/value`` percentage facts for explicit value claims.

        A percentage followed by an explicit reference period (year, month or
        quarter) keeps it as ``FactPeriod``; a percentage with no stated period
        is kept without one — except for *forecasts*, which are under-determined
        without a reference period and are therefore ignored. Share/ratio units
        ("% of GDP") are never converted into percentage facts. Returns the
        number of facts emitted (0 when the sentence is not a value claim).
        """
        if not _VALUE_GATE.search(sentence):
            return 0

        forecast = bool(_FORECAST_VERB.search(sentence))
        covered: list[tuple[int, int]] = []
        emitted = 0
        claimed = False  # a genuine value claim was present (even if deduped or ignored)

        for match in _PERCENT_WITH_QUARTER.finditer(sentence):
            token = match.group("token")
            if _is_share(sentence, match):
                continue
            claimed = True
            period = FactPeriod(
                PeriodKind.QUARTER,
                f"{match.group('year')}-{_QUARTER_NUM[match.group('quarter').lower()]}",
                label=sentence[match.start("period") : match.end("year")],
            )
            covered.append((match.start("token"), match.end("token")))
            fact = cls._emit(
                result, document, index, sentence, subject, PREDICATE_VALUE,
                percentage(cls._token_value(token), source_text=token),
                Confidence.HIGH, counters, seen, period=period, speaker=speaker,
            )
            if fact is not None:
                result.add(fact)
                emitted += 1

        for match in _PERCENT_WITH_MONTH.finditer(sentence):
            token = match.group("token")
            if _is_share(sentence, match):
                continue
            claimed = True
            period = FactPeriod(
                PeriodKind.MONTH,
                f"{match.group('year')}-{_MONTH_NUM[match.group('month').lower()]}",
                label=sentence[match.start("period") : match.end("year")],
            )
            covered.append((match.start("token"), match.end("token")))
            fact = cls._emit(
                result, document, index, sentence, subject, PREDICATE_VALUE,
                percentage(cls._token_value(token), source_text=token),
                Confidence.HIGH, counters, seen, period=period, speaker=speaker,
            )
            if fact is not None:
                result.add(fact)
                emitted += 1

        for match in _PERCENT_WITH_YEAR.finditer(sentence):
            token = match.group("token")
            if _is_share(sentence, match):
                continue
            claimed = True
            period = FactPeriod(
                PeriodKind.YEAR,
                match.group("year"),
                label=sentence[match.start("period") : match.end("year")],
            )
            covered.append((match.start("token"), match.end("token")))
            fact = cls._emit(
                result, document, index, sentence, subject, PREDICATE_VALUE,
                percentage(cls._token_value(token), source_text=token),
                Confidence.HIGH, counters, seen, period=period, speaker=speaker,
            )
            if fact is not None:
                result.add(fact)
                emitted += 1

        for match in _VALUE_TOKEN_ONLY.finditer(sentence):
            if any(start <= match.start("token") and match.end("token") <= end for start, end in covered):
                continue
            if _is_share(sentence, match):
                continue
            claimed = True
            if forecast:
                continue  # a forecast without an explicit reference period is ignored
            token = match.group("token")
            fact = cls._emit(
                result, document, index, sentence, subject, PREDICATE_VALUE,
                percentage(cls._token_value(token), source_text=token),
                Confidence.HIGH, counters, seen, speaker=speaker,
            )
            if fact is not None:
                result.add(fact)
                emitted += 1

        return emitted if emitted else claimed

    @staticmethod
    def _token_value(token: str) -> float:
        return float(re.match(r"[0-9.]+", token).group(0))

    # ------------------------------------------------------------------
    # inflation / growth / labour market
    # ------------------------------------------------------------------
    @classmethod
    def _add_inflation_facts(cls, result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, counters: dict, seen: set, speaker: str | None, *, strict: bool) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        elif "core inflation" in lower or "core hicp" in lower:
            subject = SUBJECT_CORE_INFLATION
        else:
            subject = SUBJECT_INFLATION
        if cls._add_value_facts(result, document, index, sentence, subject, counters, seen, speaker):
            return
        if not strict and _is_economic_assertion(sentence):
            cls._emit_text(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters, seen, speaker)

    @classmethod
    def _add_growth_facts(cls, result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, counters: dict, seen: set, speaker: str | None, *, strict: bool) -> None:
        if cls._add_value_facts(result, document, index, sentence, SUBJECT_GDP, counters, seen, speaker):
            return
        if not strict and _is_economic_assertion(sentence):
            cls._emit_text(result, document, index, sentence, SUBJECT_GROWTH, PREDICATE_ASSESSMENT, counters, seen, speaker)

    @classmethod
    def _add_labour_facts(cls, result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, counters: dict, seen: set, speaker: str | None, *, strict: bool) -> None:
        lower = sentence.lower()
        if "unemployment" in lower:
            subject = SUBJECT_UNEMPLOYMENT
        elif "wage" in lower:
            subject = SUBJECT_WAGES
        else:
            subject = SUBJECT_LABOUR_MARKET
        if cls._add_value_facts(result, document, index, sentence, subject, counters, seen, speaker):
            return
        if not strict and _is_economic_assertion(sentence):
            cls._emit_text(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters, seen, speaker)


def _is_share(sentence: str, match: re.Match) -> bool:
    """True when the value token is followed by a share/ratio unit ("% of
    GDP", "% of total", …) — such ratios are never stored as percentages."""
    window = sentence[match.end("token") : match.end("token") + 80]
    return bool(_SHARE_UNIT.search(window))
