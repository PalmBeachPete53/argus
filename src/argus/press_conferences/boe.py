"""Bank of England — MPC Press Conference extractor (Phase 4.x).

Extracts the facts of an official Bank of England Monetary Policy Committee
press conference transcript (a PDF published with each Monetary Policy Report)
from the normalized document text, answering "what did the Governor, Deputy
Governors and MPC members explicitly state during the press conference?":

- the Q&A answers of the MPC officials on the panel, with the verbatim speaker
  label preserved (``Fact.speaker``) and the Q&A position preserved in
  ``identity_qualifier`` (``answer:<turn>:<n>`` vs ``remarks:<n>`` — the Phase
  7 attribution contract);
- any official turn before the first journalist label is the collective
  remarks tail (``Fact.speaker = None``), exactly as for the Fed.

BoE transcript structure. The BoE MPR press conference transcript PDF is a
**turn-based dialog**: each turn is a standalone capitalized name line
followed by that speaker's words, e.g.::

    Andrew Bailey
    So with that, Dave, Clare and I will be happy to take your questions. Thank you.
    Mehreen Khan
    Hello. Mehreen Khan from The Times. Governor, for the last couple of months…
    Andrew Bailey
    Well, I'm not going to comment on the situation in the US…
    Clare Lombardelli
    Yeah. What I would add to that is…

There are **no** ``Question:`` / ``Answer:`` colon markers (ECB) and **no**
ALL-CAPS role labels (Fed): the BoE transcript identifies the speaker by name
alone. A name that is a known MPC member (``_BOE_MPC_MEMBERS``) starts an
official turn; every other name label is a **journalist / moderator** question
turn and is **never mined**. This is the BoE analog of the Fed's role-word
detection and the ECB's role+name detection: the transcript does not carry
role words, so the bank-specific closed set of MPC member names is the
conservative identity source — a name outside the set is never invented as an
official, and an official's words are never presented as a journalist's.

PDF normalization. Because the source is a PDF, page sections are line-wrapped
paragraphs and a single speaker turn commonly spans several wrapped lines (and
a page break). The line walker therefore **accumulates** a turn's lines across
sections and mines the whole turn as one paragraph, so sentence boundaries
fall on ``". "`` rather than on artificial line wraps.

Bank-specific knowledge kept here (everything else is the shared structural
helpers in ``_shared.py``):

- the BoE transcript label structure (``_LABEL_RE``, ``_BOE_MPC_MEMBERS``) and
  the turn-driven, paragraph-accumulating parser;
- the BoE forward-guidance phrasing ("data dependent", "will be guided by",
  "there will be a decision", "will form the judgment on where rates need to
  go", …);
- the BoE policy vocabulary ("Bank Rate", "the MPC", "the Bank of England",
  "monetary policy", "interest rates") and stance words ("decision",
  "unchanged", "hold", "primary tool", …);
- BoE inflation vocabulary ("CPI", "disinflation", "food prices",
  "energy prices", "second-round effects", "inflationary");
- BoE financial vocabulary ("gilt(s)", "yields", "term premia", "QT",
  "quantitative tightening", "balance sheet", "reserves");
- BoE risk vocabulary ("distribution of risk", "on the upside/downside").

Deliberately NOT extracted (Phase 7 / press-conference boundary): the decision
itself (wording, rates, changes, effective date) — Phase 5, gated on decision
publications; decision rationale — Phase 6; journalist question content;
hawkish/dovish or stance interpretation, market expectations, forex
fundamentals — none of these is ever invented here.
"""

from __future__ import annotations

import re

from ..documents.base import NormalizedDocument
from ..facts import ExtractionResult
from ._shared import (
    SUBJECT_CORE_INFLATION,
    SUBJECT_FINANCIAL_CONDITIONS,
    SUBJECT_GDP,
    SUBJECT_GROWTH,
    SUBJECT_GROWTH_RISK,
    SUBJECT_INFLATION,
    SUBJECT_INFLATION_DRIVER,
    SUBJECT_INFLATION_EXPECTATIONS,
    SUBJECT_INFLATION_RISK,
    SUBJECT_LABOUR_MARKET,
    SUBJECT_MONETARY_POLICY,
    SUBJECT_POLICY_GUIDANCE,
    SUBJECT_RISK,
    SUBJECT_UNEMPLOYMENT,
    SUBJECT_WAGES,
    FINANCIAL_ANCHORS,
    GROWTH_ANCHORS,
    INFLATION_ANCHORS,
    LABOUR_ANCHORS,
    RISK_ANCHORS,
    is_economic_assertion,
    split_sentences,
    PressConferenceReporter,
)
from .base import PressConferenceExtractor

EXTRACTION_VERSION = "7.2.0"

COVERAGE_SOURCE = (
    "https://www.bankofengland.co.uk/monetary-policy-report/<yyyy>/<month>-<yyyy> "
    "(MPR issue page, 'Press conference transcript (PDF)'). Classified via the "
    "boe_mpc_press_conference source type_hint (press_conference)."
)

# ---------------------------------------------------------------------------
# Transcript labels (BoE-specific). A standalone capitalized name line starts
# a turn. The known MPC membership (as of 2026) is the conservative official
# identity; any other label line is a journalist / moderator boundary.
# ---------------------------------------------------------------------------
_LABEL_RE = re.compile(r"^[A-Z][a-z]+(?:[ -][A-Z][a-zA-Z]*)*[.:]?\s*$")

_BOE_MPC_MEMBERS = frozenset({
    "Andrew Bailey",
    "Clare Lombardelli",
    "Dave Ramsden",
    "Sarah Breeden",
    "Huw Pill",
    "Megan Greene",
    "Catherine Mann",
    "Catherine L Mann",
    "Swati Dhingra",
    "Alan Taylor",
})


def _label_of(line: str) -> str:
    return line.strip().rstrip(".:;")


def _is_official_label(label: str) -> bool:
    return label in _BOE_MPC_MEMBERS


# A sentence-ending (turn-complete) line. A real journalist label follows the
# end of the previous answer; a wrapped PDF fragment does not.
_SENTENCE_END_RE = re.compile(r"[.?!:;\u2026]$")


def _is_label_line(line: str, prev_line: str) -> bool:
    """Conservative BoE label acceptance.

    A real label is a multi-word capitalized name that either is a known MPC
    member or sits at a clean turn boundary (start of the transcript, directly
    after a sentence-ending line, or directly after another label). Single-word
    interjections (``Yeah.``) and wrapped PDF fragments (``Charter Act.``) are
    rejected so they never become a spurious journalist turn boundary.
    """
    if not _LABEL_RE.match(line):
        return False
    if _is_official_label(_label_of(line)):
        return True
    if " " not in line.strip().rstrip(".:; "):
        return False  # single-word interjection → content, not a label
    if not prev_line:
        return True  # first line of the transcript
    if _SENTENCE_END_RE.search(prev_line):
        return True  # previous turn completed
    if _LABEL_RE.match(prev_line):
        return True  # consecutive labels (name, then name + outlet)
    return False  # wrapped fragment continuation → content


# ---------------------------------------------------------------------------
# BoE forward-guidance phrasing — BoE vernacular, distinct vocabulary, same
# structural "guidance" slot as Phase 7.
# ---------------------------------------------------------------------------
_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bstand(?:s|ing)?\s+ready\s+to\b", re.IGNORECASE),
    re.compile(r"\bwill\s+not\s+hesitate\s+to\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bmeeting\s+by\s+meeting\b", re.IGNORECASE),
    re.compile(r"\bdata[ -]?dependent\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
    re.compile(r"\bdepends?\s+(?:on|upon)\s+(?:the\s+)?(?:incoming\s+)?data\b", re.IGNORECASE),
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess|evaluate)\b", re.IGNORECASE),
    re.compile(r"\bwill\s+take\s+into\s+account\b", re.IGNORECASE),
    re.compile(r"\bwill\s+decide\b", re.IGNORECASE),
    re.compile(r"\bfuture\s+(?:policy\s+)?decisions?\b", re.IGNORECASE),
    re.compile(r"\bthere\s+will\s+be\s+a\s+decision\b", re.IGNORECASE),
    re.compile(r"\bwill\s+form\s+the\s+judgment\b", re.IGNORECASE),
    re.compile(r"\bwill\s+keep\s+(?:monetary\s+)?policy\s+under\s+review\b", re.IGNORECASE),
)

# Policy sentence: a BoE policy stance word *and* a BoE policy term, so a bare
# "growth trajectory" or "policy" is never mined as policy.
_POLICY_TERM = re.compile(
    r"\b(?:"
    r"bank\s+rate"
    r"|monetary(?:\s+policy)?"
    r"|interest\s+rates?"
    r"|the\s+mpc"
    r"|the\s+bank\s+of\s+england"
    r"|the\s+bank"
    r"|policy\s+rates?"
    r"|quantitative\s+tightening"
    r")\b",
    re.IGNORECASE,
)
_POLICY_STANCE = re.compile(
    r"\bstance\b"
    r"|\bdecided\s+to\b"
    r"|\bdecision(?:s)?\b"
    r"|\bappropriate\b"
    r"|\brestrictive\b"
    r"|\baccommodative\b"
    r"|\btightening\b"
    r"|\beasing\b"
    r"|\bunchanged\b"
    r"|\bhold\b"
    r"|\bprimary\s+tool\b",
    re.IGNORECASE,
)

# BoE category vocabulary on top of the generic structural anchors.
_INFLATION_EXTRAS: tuple[re.Pattern, ...] = (
    re.compile(r"\bcpi\b", re.IGNORECASE),
    re.compile(r"\bdisinflation\b", re.IGNORECASE),
    re.compile(r"\bfood\s+prices?\b", re.IGNORECASE),
    re.compile(r"\benergy\s+prices?\b", re.IGNORECASE),
    re.compile(r"\bsecond[- ]round effects\b", re.IGNORECASE),
    re.compile(r"\binflationary\b", re.IGNORECASE),
)

_FINANCIAL_EXTRAS: tuple[re.Pattern, ...] = (
    re.compile(r"\bgilts?\b", re.IGNORECASE),
    re.compile(r"\byields?\b", re.IGNORECASE),
    re.compile(r"\bterm\s+premi[ae]\b", re.IGNORECASE),
    re.compile(r"\bquantitative\s+tightening\b", re.IGNORECASE),
    re.compile(r"\bqt\b", re.IGNORECASE),
    re.compile(r"\bbalance\s+sheet\b", re.IGNORECASE),
    re.compile(r"\breserves?\b", re.IGNORECASE),
)

_RISK_EXTRAS: tuple[re.Pattern, ...] = (
    re.compile(r"\bdistribution\s+of\s+risk", re.IGNORECASE),
    re.compile(r"\bon\s+the\s+(?:upside|downside)\b", re.IGNORECASE),
)

_INFLATION_DRIVER = re.compile(
    r"\b(?:driven|driving|drivers?|owing to|boosted by|weighed on|second[- ]round effects)\b"
    r"|\b(?:energy|food|oil|gas|services?)\s+prices?\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Category precedence (deterministic): guidance > policy > risk > financial >
# inflation > labour > growth.
# ---------------------------------------------------------------------------
CAT_NONE = "none"
CAT_GUIDANCE = "guidance"
CAT_POLICY = "policy"
CAT_RISK = "risk"
CAT_FINANCIAL = "financial"
CAT_INFLATION = "inflation"
CAT_LABOUR = "labour"
CAT_GROWTH = "growth"


class _RunState:
    """Run state threaded through the turn walker.

    ``pending`` accumulates a speaker turn's wrapped lines across PDF page
    sections, so a turn spanning a page break is mined as a single paragraph.
    """

    __slots__ = (
        "remarks_content",
        "qna_content",
        "risk_found",
        "guidance_found",
        "seen_journalist",
        "mode",
        "turn_counter",
        "current_speaker",
        "current_turn",
        "current_section",
        "prev_line",
        "pending",
    )

    def __init__(self) -> None:
        self.remarks_content = False
        self.qna_content = False
        self.risk_found = False
        self.guidance_found = False
        self.seen_journalist = False
        self.mode = "remarks"  # remarks until the first journalist label
        self.turn_counter = 0
        self.current_speaker: str | None = None
        self.current_turn = 0
        self.current_section = 0
        self.prev_line = ""
        self.pending: list[str] = []


class BoEPressConferenceExtractor(PressConferenceExtractor):
    bank = "boe"
    extraction_version = EXTRACTION_VERSION

    def extract(self, publication, document: NormalizedDocument) -> ExtractionResult:
        result = ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )
        if not document.sections:
            result.warnings.append("no_sections")
            return result

        reporter = PressConferenceReporter(extraction_version=EXTRACTION_VERSION)
        state = _RunState()

        for index, section in enumerate(document.sections):
            self._walk_lines(result, document, index, section.text or "", reporter, state)
        self._flush_pending(result, document, state.current_section, reporter, state)

        if not state.remarks_content:
            result.warnings.append("no_remarks")
        if not state.qna_content:
            result.warnings.append("no_qna")
        if not state.risk_found:
            result.warnings.append("no_risk_assessment")
        if not state.guidance_found:
            result.warnings.append("no_forward_guidance")
        return result

    # ------------------------------------------------------------------
    # turn-driven line walking — remarks vs Q&A, speaker attribution
    # ------------------------------------------------------------------
    def _walk_lines(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        text: str,
        reporter: PressConferenceReporter,
        state: _RunState,
    ) -> None:
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            if _is_label_line(line, state.prev_line):
                label = _label_of(line)
                self._flush_pending(result, document, state.current_section, reporter, state)
                state.current_section = index
                if _is_official_label(label):
                    # MPC member: remarks until the first journalist label,
                    # afterwards an answer of the current Q&A turn.
                    state.current_speaker = label
                    if state.seen_journalist:
                        state.mode = "qna"
                    state.current_turn = state.turn_counter
                else:
                    # Journalist / moderator label: new Q&A turn, never mined.
                    state.seen_journalist = True
                    state.mode = "qna"
                    state.turn_counter += 1
                    state.current_turn = state.turn_counter
                    state.current_speaker = None
                state.prev_line = line
                continue

            # Plain (wrapped) content line of the current turn.
            state.prev_line = line
            if state.current_speaker is None:
                continue  # journalist question continuation → never mined
            if state.mode == "qna" and state.current_turn == 0:
                # Content with no journalist turn yet would have been remarks;
                # an unprefixed line before any turn is unattributed → skip.
                continue
            state.pending.append(line)

    def _flush_pending(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        reporter: PressConferenceReporter,
        state: _RunState,
    ) -> None:
        if not state.pending:
            return
        content = " ".join(state.pending).strip()
        state.pending = []
        if not content:
            return
        context = "remarks" if state.mode == "remarks" else f"answer:{state.current_turn}"
        speaker = None if state.mode == "remarks" else state.current_speaker
        self._mine_content(result, document, index, content, reporter, state, speaker=speaker, context=context)

    def _mine_content(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        content: str,
        reporter: PressConferenceReporter,
        state: _RunState,
        *,
        speaker: str | None,
        context: str,
    ) -> None:
        if context == "remarks":
            state.remarks_content = True
        else:
            state.qna_content = True
        for sentence in split_sentences(content):
            self._mine_sentence(result, document, index, sentence, reporter, state, speaker=speaker, context=context)

    # ------------------------------------------------------------------
    # sentence classification and fact emission (content-first precedence)
    # ------------------------------------------------------------------
    def _mine_sentence(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        reporter: PressConferenceReporter,
        state: _RunState,
        *,
        speaker: str | None,
        context: str,
    ) -> None:
        category = self._categorize(sentence)
        if category == CAT_GUIDANCE:
            state.guidance_found = True
            reporter.emit_text(
                result, document, index, sentence, SUBJECT_POLICY_GUIDANCE, "statement",
                speaker=speaker, context=context,
            )
        elif category == CAT_POLICY:
            reporter.emit_text(
                result, document, index, sentence, SUBJECT_MONETARY_POLICY, "statement",
                speaker=speaker, context=context,
            )
        elif category == CAT_RISK:
            state.risk_found = True
            subject = self._risk_subject(sentence)
            reporter.emit_risk(result, document, index, sentence, subject, speaker=speaker, context=context)
        elif category == CAT_FINANCIAL:
            if not reporter.emit_value_facts(result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, speaker=speaker, context=context):
                if is_economic_assertion(sentence):
                    reporter.emit_text(
                        result, document, index, sentence, SUBJECT_FINANCIAL_CONDITIONS, "assessment",
                        speaker=speaker, context=context,
                    )
        elif category == CAT_INFLATION:
            self._add_inflation_facts(result, document, index, sentence, reporter, speaker=speaker, context=context)
        elif category == CAT_LABOUR:
            self._add_labour_facts(result, document, index, sentence, reporter, speaker=speaker, context=context)
        elif category == CAT_GROWTH:
            if not reporter.emit_value_facts(result, document, index, sentence, SUBJECT_GDP, speaker=speaker, context=context):
                if is_economic_assertion(sentence):
                    reporter.emit_text(
                        result, document, index, sentence, SUBJECT_GROWTH, "assessment",
                        speaker=speaker, context=context,
                    )

    def _categorize(self, sentence: str) -> str:
        if self._matches(_GUIDANCE_ANCHORS, sentence):
            return CAT_GUIDANCE
        if self._is_policy_sentence(sentence):
            return CAT_POLICY
        if self._matches(RISK_ANCHORS, sentence) or self._matches(_RISK_EXTRAS, sentence):
            return CAT_RISK
        if self._matches(FINANCIAL_ANCHORS, sentence) or self._matches(_FINANCIAL_EXTRAS, sentence):
            return CAT_FINANCIAL
        if self._matches(INFLATION_ANCHORS, sentence) or self._matches(_INFLATION_EXTRAS, sentence):
            return CAT_INFLATION
        if self._matches(LABOUR_ANCHORS, sentence):
            return CAT_LABOUR
        if self._matches(GROWTH_ANCHORS, sentence):
            return CAT_GROWTH
        return CAT_NONE

    @staticmethod
    def _matches(anchors, sentence: str) -> bool:
        return any(anchor.search(sentence) for anchor in anchors)

    def _is_policy_sentence(self, sentence: str) -> bool:
        return bool(_POLICY_STANCE.search(sentence) and _POLICY_TERM.search(sentence))

    @staticmethod
    def _risk_subject(sentence: str) -> str:
        lower = sentence.lower()
        if "inflation" in lower:
            return SUBJECT_INFLATION_RISK
        if "growth" in lower or "activity" in lower or "gdp" in lower:
            return SUBJECT_GROWTH_RISK
        return SUBJECT_RISK

    # ------------------------------------------------------------------
    # inflation / labour subject routing
    # ------------------------------------------------------------------
    def _add_inflation_facts(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        reporter: PressConferenceReporter,
        *,
        speaker: str | None,
        context: str,
    ) -> None:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            subject = SUBJECT_INFLATION_EXPECTATIONS
        elif "core inflation" in lower or "underlying inflation" in lower:
            subject = SUBJECT_CORE_INFLATION
        elif _INFLATION_DRIVER.search(sentence):
            subject = SUBJECT_INFLATION_DRIVER
        else:
            subject = SUBJECT_INFLATION
        if reporter.emit_value_facts(result, document, index, sentence, subject, speaker=speaker, context=context):
            return
        if is_economic_assertion(sentence):
            reporter.emit_text(result, document, index, sentence, subject, "assessment", speaker=speaker, context=context)

    def _add_labour_facts(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        reporter: PressConferenceReporter,
        *,
        speaker: str | None,
        context: str,
    ) -> None:
        lower = sentence.lower()
        if "unemployment" in lower:
            subject = SUBJECT_UNEMPLOYMENT
        elif "wage" in lower:
            subject = SUBJECT_WAGES
        else:
            subject = SUBJECT_LABOUR_MARKET
        if reporter.emit_value_facts(result, document, index, sentence, subject, speaker=speaker, context=context):
            return
        if is_economic_assertion(sentence):
            reporter.emit_text(result, document, index, sentence, subject, "assessment", speaker=speaker, context=context)