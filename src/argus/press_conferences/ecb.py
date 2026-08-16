"""ECB — Press Conference extractor (Phase 4.3).

Extracts the facts of an ECB press conference transcript from the normalized
document text, answering "what did the central bank explicitly say during the
press conference?":

- the **introductory statement** (remarks): the collective statement read on
  behalf of the Governing Council — inflation, growth, labour market, policy
  stance, risks, financial conditions, forward guidance;
- the **Q&A answers**: the officials' individual answers to journalists, with
  the verbatim speaker attribution preserved when the document labels one
  (``Fact.speaker``) and the Q&A position preserved in ``identity_qualifier``
  (``answer:<turn>:<n>`` vs ``remarks:<n>``).

Sections are routed conservatively: a known remarks heading ("Introductory
statement" and its ECB synonyms) is remarks, a known Q&A heading ("Questions and
answers" and its synonyms) is Q&A, and an **unknown heading is mined only when
the text carries a reliable Q&A signal** (``Question:`` / ``Answer:`` lines) —
otherwise the section is **ignored** ("absence of proof → absence of
extraction"). An unknown section is never assumed to be remarks, so a future
appendix / biography / legal notice / closing-remarks section yields no fact.

Content is classified sentence-by-sentence into the Phase 4.3 categories
(A–G) with a deterministic precedence: forward guidance (G) > policy stance
(D) > risks (E) > financial conditions (F) > inflation (A) > labour market (C)
> growth (B). A sentence matching no category produces no fact — reliability is
preferred over coverage.

Deliberately NOT extracted (Phase 4.3 boundary):

- the decision itself (wording, rates, changes, effective date) — Phase 4.1,
  gated on decision publications
- the decision rationale — Phase 4.2 territory (monetary policy statement)
- journalist question content: the questions are never mined, so a market-fact
  sentence in a question is never attributed to the bank unless the official's
  answer restates it
- hawkish/dovish or stance interpretation, market expectations, forex
  fundamentals, probability conversion ("we will assess the incoming data" is a
  verbatim guidance fact, never a "rate hike expected" fact) — none of these is
  ever invented here
- non-economic questions (and their answers): a question flagged as
  non-economic by an explicit personal marker (memoir, personal/private life,
  family life, your family/retirement/spouse/children/partner, hobbies) skips
  the whole turn (warning ``non_economic_question_skipped``). Generic tokens
  such as "personal" or "personally" never trigger the skip on their own — they
  appear naturally in economic questions and must not suppress an answer.

Design rules

- No fact is invented. A value/orientation is only produced when the source
  states it, and every Fact preserves an *exact verbatim* supporting passage
  (``source_text``) copied from the normalized document.
- ``Fact.speaker`` preserves the verbatim official label (e.g. "President
  Christine Lagarde") when a Q&A answer is labelled; an unlabelled answer or a
  collective statement carries ``speaker=None`` (never inferred).
- Quantitative facts carry ``FactPeriod`` only when the source states an
  explicit reference period; the "2% target"/"close to 2%" phrasing is never
  mined as a value — only sentences with an explicit value claim
  ("projected/expected/stood at …") are.
- Risk facts are categorical (``upside`` / ``downside`` / ``balanced``) only
  when the source states an explicit orientation; otherwise they are verbatim
  text assessments. "Uncertainty remains high" is never turned into a
  directional risk.
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
from .base import PressConferenceExtractor

EXTRACTION_VERSION = "7.0.0"

# ---------------------------------------------------------------------------
# Canonical Phase 4.3 subjects (controlled vocabulary, see docs/EXTRACTORS.md).
# Overlapping content types reuse the Phase 4.1/6 subjects (``policy_guidance``,
# ``inflation``, ``core_inflation``, ``inflation_expectations``, ``growth``,
# ``gdp``, ``labour_market``, ``unemployment``, ``wages``,
# ``financial_conditions``, ``risk``, ``inflation_risk``, ``growth_risk``).
# New in Phase 4.3: ``inflation_driver`` (A — factors behind inflation) and the
# ``monetary_policy`` / ``statement`` predicate pair (D — policy stance and
# conditions discussed at the press conference, kept verbatim).
# ---------------------------------------------------------------------------
SUBJECT_INFLATION = "inflation"
SUBJECT_CORE_INFLATION = "core_inflation"
SUBJECT_INFLATION_EXPECTATIONS = "inflation_expectations"
SUBJECT_INFLATION_DRIVER = "inflation_driver"
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
# Remarks vs Q&A. A section is routed by its normalized heading; when the
# heading carries no signal the mode is inferred from the text (Q&A markers).
#
# Routing is CONSERVATIVE (Phase 4.3 hardening): a section whose mode cannot be
# determined with sufficient certainty is IGNORED rather than assumed to be
# remarks. "Absence of proof → absence of extraction": an unknown heading
# without a reliable Q&A signal never becomes remarks, so a future section
# (appendix, biography, legal notice, closing remarks, …) is simply not mined.
# "Closing Remarks" is deliberately NOT a remarks heading: only the known ECB
# variants ("Introductory statement" and its synonyms) are.
# ---------------------------------------------------------------------------
MODE_REMARKS = "remarks"
MODE_QNA = "qna"
MODE_IGNORE = "ignore"

_REMARKS_HEADINGS = frozenset({
    "introductory statement",
    "opening statement",
    "introductory remarks",
    "opening remarks",
})
_QNA_HEADINGS = frozenset({
    "questions and answers",
    "questions",
    "question",
    "answers",
    "answers to questions",
    "q&a",
    "questions from",
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


def _section_mode(heading: str, text: str) -> str:
    t = _clean_heading(heading)
    if not t:
        return _mode_from_text(text)
    if t in _REMARKS_HEADINGS:
        return MODE_REMARKS
    if t in _QNA_HEADINGS:
        return MODE_QNA
    return _mode_from_text(text)


def _mode_from_text(text: str) -> str:
    for line in (text or "").split("\n"):
        if _QUESTION_PREFIX.match(line) or _ANSWER_PREFIX.match(line):
            return MODE_QNA
    return MODE_IGNORE


# ---------------------------------------------------------------------------
# Transcript label detection (ECB-specific structure).
#
# - "Question: …" / "Answer: …" lines (colon required — a natural sentence
#   like "Question marks remain over the outlook…" is never a Q&A marker) mark
#   the Q&A turn boundaries. Question content is the journalist's, never mined.
# - Role + name labels ("President Christine Lagarde", "Vice-President Luis de
#   Guindos") identify the official answering; the label is preserved verbatim
#   in ``Fact.speaker``, never invented. An unlabelled answer keeps
#   ``speaker=None`` (the answer is still the central bank's, but no name is
#   guessed).
# ---------------------------------------------------------------------------
_QUESTION_PREFIX = re.compile(r"^\s*question\s*:\s*(?P<content>.*)$", re.IGNORECASE)
_ANSWER_PREFIX = re.compile(r"^\s*answer\s*:\s*(?P<content>.*)$", re.IGNORECASE)

_SPEAKER_ROLE = (
    r"(?:vice[-\s]?president|president|governor|chair(?:man|woman)?|"
    r"member of the executive board|executive board member)"
)
# 1–5 tokens after the role: capitalized name words or short lowercase particles
# ("de", "van", …). A standalone label may carry a trailing colon/period.
_SPEAKER_NAME = r"(?:[A-ZÀ-Þ]\w*|[a-zà-ÿ]{1,3})"
_SPEAKER_WITH_CONTENT = re.compile(
    rf"^\s*(?P<speaker>{_SPEAKER_ROLE}(?:\s+{_SPEAKER_NAME}){{1,5}})\s*:\s*(?P<content>.+)$",
    re.IGNORECASE,
)
_SPEAKER_STANDALONE = re.compile(
    rf"^\s*(?P<speaker>{_SPEAKER_ROLE}(?:\s+{_SPEAKER_NAME}){{1,5}})\s*[:.]?\s*$",
    re.IGNORECASE,
)


def _match_speaker_label(line: str) -> tuple[str, str] | None:
    m = _SPEAKER_WITH_CONTENT.match(line)
    if m is not None:
        return m.group("speaker"), m.group("content")
    m = _SPEAKER_STANDALONE.match(line)
    if m is not None:
        return m.group("speaker"), ""
    return None


# Non-economic questions (explicit, conservative multi-word markers): the whole
# turn — the question and the answer relating to it — is skipped. Generic
# personal-language tokens ("personal", "personally", "private") are deliberately
# NOT used on their own: they occur naturally in economic questions ("What is
# your personal assessment of the inflation outlook?") and must never suppress an
# answer. Only clearly personal topics trigger the skip: a memoir, personal /
# private life (or matters/affairs), family life, your family/retirement/
# spouse/children/partner, or hobbies. Prefer reliability over coverage: an
# incidental economic word ("growth", "prices") in a non-economic answer never
# produces a fact.
_NON_ECONOMIC_QUESTION = re.compile(
    r"\b(?:"
    r"memoirs?|"
    r"(?:personal|private)\s+(?:life|matters?|affairs?)|"
    r"(?:your\s+)?family\s+life|"
    r"your\s+(?:family|retirement|spouse|children|partner)|"
    r"hobbies?"
    r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Category anchors. Deterministic precedence (first match wins): guidance (G) >
# policy stance (D) > risks (E) > financial conditions (F) > inflation (A) >
# labour market (C) > growth (B). A sentence matching none is ignored.
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
    re.compile(r"\bstands?\s+ready\s+to\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bwill\s+not\s+hesitate\s+to\b", re.IGNORECASE),
    re.compile(r"\bwill\s+keep\s+(?:the\s+)?(?:key\s+)?(?:ecb\s+)?interest\s+rates?\b", re.IGNORECASE),
    re.compile(r"\bwill\s+not\s+pre-?commit\b", re.IGNORECASE),
    re.compile(r"\bmeeting\s+by\s+meeting\b", re.IGNORECASE),
    re.compile(r"\bmeeting\s*-\s*after\s*-\s*meeting\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
    re.compile(r"\bwill\s+depend\s+on\b", re.IGNORECASE),
    re.compile(r"\bdata-?dependent\b", re.IGNORECASE),
    re.compile(r"\bwill\s+(?:assess|monitor)\s+(?:the\s+)?(?:incoming\s+)?data\b", re.IGNORECASE),
    re.compile(r"\bwill\s+decide\b", re.IGNORECASE),
    re.compile(r"\bfuture\s+(?:policy\s+)?decisions\b", re.IGNORECASE),
    re.compile(r"\bpolicy\s+path\b", re.IGNORECASE),
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess|follow)\b", re.IGNORECASE),
    re.compile(r"\bexpects?\s+(?:the\s+)?(?:key\s+)?(?:ecb\s+)?interest\s+rates?\s+to\s+remain\b", re.IGNORECASE),
)

# D — policy stance/conditions/trajectory. Compound signal: a policy stance word
# *and* a policy term, so "the growth trajectory" alone is never mined as policy.
_POLICY_TERM = re.compile(r"\b(?:policy|monetary|rate|rates|governing council|council)\b", re.IGNORECASE)
_POLICY_STANCE = re.compile(
    r"\bstance\b"
    r"|\bpre-?commit(?:ment|ting)?\b"
    r"|\bdetermined\s+to\s+ensure\b"
    r"|\bcommitted\s+to\b"
    r"|\bappropriate\b"
    r"|\brestrictive\b"
    r"|\baccommodative\b"
    r"|\btightening\b"
    r"|\beasing\b",
    re.IGNORECASE,
)


def _is_policy_sentence(sentence: str) -> bool:
    return bool(_POLICY_STANCE.search(sentence) and _POLICY_TERM.search(sentence))


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

_INFLATION_DRIVER = re.compile(
    r"\b(?:driven|driving|drivers?|owing to|boosted by|weighed on)\b"
    r"|\b(?:energy|food|oil|gas|services?|import(?:ed)?)\s+prices?\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Quantitative values. A sentence is mined for percentages only when it states
# an explicit value claim (Phase 4.2 value-gate principle), so "the 2% target",
# "close to 2%" or "converging towards 2%" is never read as a value.
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

    __slots__ = ("remarks_content", "qna_content", "risk_found", "guidance_found", "non_economic_skipped")

    def __init__(self) -> None:
        self.remarks_content = False
        self.qna_content = False
        self.risk_found = False
        self.guidance_found = False
        self.non_economic_skipped = False


class EcbPressConferenceExtractor(PressConferenceExtractor):
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
            mode = _section_mode(section.heading or "", section.text or "")
            self._process_lines(result, document, index, section.text or "", mode, counters, state)

        if not state.remarks_content:
            result.warnings.append("no_remarks")
        if not state.qna_content:
            result.warnings.append("no_qna")
        if not state.risk_found:
            result.warnings.append("no_risk_assessment")
        if not state.guidance_found:
            result.warnings.append("no_forward_guidance")
        if state.non_economic_skipped:
            result.warnings.append("non_economic_question_skipped")
        return result

    # ------------------------------------------------------------------
    # line walking — remarks vs Q&A, speaker attribution
    # ------------------------------------------------------------------
    def _process_lines(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        text: str,
        mode: str,
        counters: dict[str, int],
        state: _RunState,
    ) -> None:
        if mode == MODE_IGNORE:
            return
        turn = 0
        current_speaker: str | None = None
        in_question = False
        in_answer = False
        skip_turn = False

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            question = _QUESTION_PREFIX.match(line)
            answer = _ANSWER_PREFIX.match(line)
            speaker = _match_speaker_label(line)

            if question is not None:
                in_question, in_answer = True, False
                turn += 1
                current_speaker = None
                skip_turn = bool(_NON_ECONOMIC_QUESTION.search(line))
                if skip_turn:
                    state.non_economic_skipped = True
                continue

            if answer is not None:
                in_question, in_answer = False, True
                if skip_turn:
                    continue
                content = (answer.group("content") or "").strip()
                if content:
                    self._mine_content(result, document, index, content, counters, state, mode, speaker=current_speaker, turn=turn)
                continue

            if speaker is not None:
                current_speaker = speaker[0]
                if mode == MODE_QNA and in_question:
                    in_question, in_answer = False, True
                if skip_turn:
                    continue
                content = (speaker[1] or "").strip()
                if content:
                    if mode == MODE_REMARKS:
                        self._mine_content(result, document, index, content, counters, state, mode, speaker=None, turn=turn)
                    else:
                        self._mine_content(result, document, index, content, counters, state, mode, speaker=current_speaker, turn=turn)
                continue

            # plain content line
            if mode == MODE_REMARKS:
                self._mine_content(result, document, index, line, counters, state, mode, speaker=None, turn=turn)
            elif in_answer and not skip_turn:
                self._mine_content(result, document, index, line, counters, state, mode, speaker=current_speaker, turn=turn)
            # else: question continuation (journalist) → never mined

    def _mine_content(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        content: str,
        counters: dict[str, int],
        state: _RunState,
        mode: str,
        *,
        speaker: str | None,
        turn: int,
    ) -> None:
        context = "remarks" if mode == MODE_REMARKS else f"answer:{turn}"
        if mode == MODE_REMARKS:
            state.remarks_content = True
        else:
            state.qna_content = True
        for sentence in _split_sentences(content):
            self._mine_sentence(result, document, index, sentence, counters, state, speaker=speaker, context=context)

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
        *,
        speaker: str | None,
        context: str,
    ) -> None:
        category = self._categorize(sentence)
        if category == CAT_GUIDANCE:
            state.guidance_found = True
            result.add(
                self._text_fact(result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, PREDICATE_STATEMENT, counters, speaker, context)
            )
        elif category == CAT_POLICY:
            result.add(
                self._text_fact(result, document, index, sentence, SUBJECT_MONETARY_POLICY, PREDICATE_STATEMENT, counters, speaker, context)
            )
        elif category == CAT_RISK:
            state.risk_found = True
            self._add_risk_facts(result, document, index, sentence, counters, speaker, context)
        elif category == CAT_FINANCIAL:
            if not self._add_value_facts(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, counters, speaker, context):
                result.add(
                    self._text_fact(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, PREDICATE_ASSESSMENT, counters, speaker, context)
                )
        elif category == CAT_INFLATION:
            self._add_inflation_facts(result, document, index, sentence, counters, speaker, context)
        elif category == CAT_LABOUR:
            self._add_labour_facts(result, document, index, sentence, counters, speaker, context)
        elif category == CAT_GROWTH:
            self._add_growth_facts(result, document, index, sentence, counters, speaker, context)

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
    def _text_fact(cls, result, document, index, sentence, subject, predicate, counters, speaker, context) -> Fact:
        return cls._fact(
            result, document, index, sentence, subject, predicate,
            FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
            Confidence.MEDIUM, counters, speaker=speaker, context=context,
        )

    @staticmethod
    def _fact(result, document, index: int, sentence: str, subject: str, predicate: str, value, confidence, counters: dict, *, period=None, speaker: str | None = None, context: str = "") -> Fact:
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
            speaker=speaker,
            identity_qualifier=qualifier,
        )

    # ------------------------------------------------------------------
    # risk assessment
    # ------------------------------------------------------------------
    @classmethod
    def _add_risk_facts(cls, result, document, index: int, sentence: str, counters: dict, speaker, context) -> None:
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
                    Confidence.HIGH, counters, speaker=speaker, context=context,
                )
            )
        else:
            result.add(cls._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters, speaker, context))

    # ------------------------------------------------------------------
    # quantitative value claims
    # ------------------------------------------------------------------
    @classmethod
    def _add_value_facts(cls, result, document, index: int, sentence: str, subject: str, counters: dict, speaker, context) -> int:
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
                    Confidence.HIGH, counters, period=period, speaker=speaker, context=context,
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
                    Confidence.HIGH, counters, period=period, speaker=speaker, context=context,
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
                    Confidence.HIGH, counters, speaker=speaker, context=context,
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
    def _add_inflation_facts(cls, result, document, index: int, sentence: str, counters: dict, speaker, context) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        elif "core inflation" in lower or "core hicp" in lower:
            subject = SUBJECT_CORE_INFLATION
        elif _INFLATION_DRIVER.search(sentence):
            subject = SUBJECT_INFLATION_DRIVER
        else:
            subject = SUBJECT_INFLATION
        if cls._add_value_facts(result, document, index, sentence, subject, counters, speaker, context):
            return
        result.add(cls._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters, speaker, context))

    @classmethod
    def _add_growth_facts(cls, result, document, index: int, sentence: str, counters: dict, speaker, context) -> None:
        if cls._add_value_facts(result, document, index, sentence, SUBJECT_GDP, counters, speaker, context):
            return
        result.add(cls._text_fact(result, document, index, sentence, SUBJECT_GROWTH, PREDICATE_ASSESSMENT, counters, speaker, context))

    @classmethod
    def _add_labour_facts(cls, result, document, index: int, sentence: str, counters: dict, speaker, context) -> None:
        lower = sentence.lower()
        if "unemployment" in lower:
            subject = SUBJECT_UNEMPLOYMENT
        elif "wage" in lower:
            subject = SUBJECT_WAGES
        else:
            subject = SUBJECT_LABOUR_MARKET
        if cls._add_value_facts(result, document, index, sentence, subject, counters, speaker, context):
            return
        result.add(cls._text_fact(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, counters, speaker, context))
