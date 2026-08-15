"""Federal Reserve — FOMC Press Conference extractor (Phase 4.x).

Extracts the facts of a Federal Reserve FOMC press conference transcript from
the normalized document text, answering "what did the Chair and other Fed
officials explicitly state during the press conference?":

- the **opening remarks** (the Chair's statement before any journalist turn):
  the collective FOMC communication — inflation, growth, labour market, policy
  stance, risks, financial conditions, forward guidance;
- the **Q&A answers**: the officials' individual answers to journalists, with
  the verbatim ALL-CAPS speaker label preserved (``Fact.speaker``) and the
  Q&A position preserved in ``identity_qualifier`` (``answer:<turn>:<n>`` vs
  ``remarks:<n>`` — the Phase 7 attribution contract).

Fed transcript structure. The transcript is a **turn-based dialog**: each turn
is a speech-verbatim ALL-CAPS speaker label followed by that speaker's words,
e.g.::

    CHAIRMAN WARSH.
    Good afternoon, everyone. Before turning to your questions …
    CHRIS RUGABER.
    Mr. Chairman, is the Committee still considering another rise …
    CHAIRMAN WARSH.
    We will decide meeting by meeting as new data arrive.
    VICE CHAIR DONALD LERNER.
    Let me add that inflation expectations remain well anchored.

There are **no** ``Question:`` / ``Answer:`` colon markers (unlike the ECB
reference transcript); the speaker label alone starts a turn. A Fed-official
label is one whose ALL-CAPS line contains a role word
(``CHAIRMAN`` / ``CHAIRWOMAN`` / ``CHAIR`` / ``VICE CHAIR`` / ``GOVERNOR`` /
``PRESIDENT``); every other ALL-CAPS name label is a **journalist / moderator**
question turn.

Mode is line-driven: the first Fed-official turn before any journalist label is
**remarks** (collective, ``Fact.speaker = None``); after the first journalist
label every Fed-official turn is a **Q&A answer** (attributed verbatim). The
Q&A turn counter increments at each journalist label. A journalist question is
**never mined** — a market-fact sentence in a question is never attributed to
the bank unless the official's answer restates it.

Ambiguous labels (``MR. POWELL.``, ``MS. YELLEN.``, a ``Mr.``/``Ms.``-form
label) are treated as **non-Fed**: they are a conservative identity, treated as
a journalist turn boundary, and no speaker is invented.

Bank-specific knowledge kept here (everything else is the shared structural
helpers in ``_shared.py``):

- the Fed transcript label structure (``_FED_ROLE_RE``, ``_ALL_CAPS_LABEL_RE``)
  and the turn-driven parsing;
- the FOMC guidance phrasing ("as appropriate", "meeting by meeting",
  "data dependent", "will not hesitate to", "will be patient", …);
- the Fed policy vocabulary ("the FOMC", "the Committee", "the Federal
  Reserve", "the funds rate", "interest rates", …);
- Fed inflation vocabulary ("PCE", "core inflation", "consumer prices") on top
  of the generic English inflation anchors.

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

EXTRACTION_VERSION = "7.1.0"

COVERAGE_SOURCE = (
    "https://www.federalreserve.gov/mediacenter/files/FOMCpresconf<date>.pdf "
    "(FOMC press conference transcript); event pages "
    "https://www.federalreserve.gov/newsevents/pressconferences/fomc-press-conference-<date>.htm. "
    "Classified via the Fed-specific 'press_conference' TypeRule "
    "(url /FOMCpresconf/ and /press-?conference/)."
)

# ---------------------------------------------------------------------------
# Transcript labels (Fed-specific). An ALL-CAPS label line names the speaker;
# the label is preserved verbatim in ``Fact.speaker`` (ALL-CAPS, trailing
# period stripped), never invented or reformatted.
# ---------------------------------------------------------------------------
_FED_ROLE_RE = re.compile(
    r"\b(?:CHAIRMAN|CHAIRWOMAN|VICE CHAIR|VICE-CHAIR|CHAIR|GOVERNOR|PRESIDENT)\b"
)

# A speech-verbatim ALL-CAPS label line: single letter / word line in caps,
# optional trailing period. Case-sensitive — a mixed-case prose line is never a
# label.
_ALL_CAPS_LABEL_RE = re.compile(
    r"^[A-Z0-9][A-Z0-9 .'\u2019-]{1,}[.:;]?\s*$"
)


def _label_of(line: str) -> str:
    return line.strip().rstrip(".:;")


def _is_fed_label(line: str) -> bool:
    return bool(_FED_ROLE_RE.search(line))


# ---------------------------------------------------------------------------
# FOMC guidance phrasing — Fed vernacular, distinct vocabulary, same structural
# "guidance" slot as Phase 7.
# ---------------------------------------------------------------------------
_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bast\s+appropriate\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+patient\b", re.IGNORECASE),
    re.compile(r"\bmeeting\s+by\s+meeting\b", re.IGNORECASE),
    re.compile(r"\bdata[ -]?dependent\b", re.IGNORECASE),
    re.compile(r"\bdepends?\s+(?:on|upon)\s+the\s+data\b", re.IGNORECASE),
    re.compile(r"\bwill\s+not\s+hesitate\s+to\b", re.IGNORECASE),
    re.compile(r"\bstand(?:s|ing)?\s+ready\s+to\b", re.IGNORECASE),
    re.compile(r"\bwill\s+continue\s+to\s+(?:monitor|assess|evaluate)\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+(?:necessary|needed)\b", re.IGNORECASE),
    re.compile(r"\bfuture\s+(?:meetings?|decisions?|policy)\b", re.IGNORECASE),
    re.compile(r"\bwill\s+take\s+into\s+account\b", re.IGNORECASE),
    re.compile(r"\bwill\s+take\s+(?:the\s+)?(?:incoming\s+)?data\s+into\s+account\b", re.IGNORECASE),
)

_POLICY_TERM = re.compile(
    r"\b(?:"
    r"monetary(?:\s+policy)?"
    r"|monetary\s+policy\s+stance"
    r"|interest\s+rates?"
    r"|federal\s+funds\s+rate"
    r"|funds\s+rate"
    r"|the\s+fed(?:eral\s+reserve)?"
    r"|the\s+fomc"
    r"|the\s+committee"
    r"|policy\s+rates?"
    r"|federal\s+reserve"
    r"|fomc"
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
    r"|\beasing\b",
    re.IGNORECASE,
)
_POLICY_STANCE_PHRASE = re.compile(
    r"\b(?:monetary\s+policy\s+stance|policy\s+stance)\b"
    r"|\b(?:appropriate|restrictive|accommodative|neutral|loose)\s+stance\s+of\s+(?:monetary\s+)?policy\b",
    re.IGNORECASE,
)

_INFLATION_EXTRAS: tuple[re.Pattern, ...] = (
    re.compile(r"\bpce\b", re.IGNORECASE),
    re.compile(r"\bconsumer\s+prices?\b", re.IGNORECASE),
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
    """Run state threaded through the turn walker."""

    __slots__ = (
        "remarks_content",
        "qna_content",
        "risk_found",
        "guidance_found",
        "seen_journalist",
        "mode",
        "turn_counter",
        "last_fed_label",
    )

    def __init__(self) -> None:
        self.remarks_content = False
        self.qna_content = False
        self.risk_found = False
        self.guidance_found = False
        self.seen_journalist = False
        self.mode = "remarks"  # remarks until the first journalist label
        self.turn_counter = 0
        self.last_fed_label: str | None = None


class FedPressConferenceExtractor(PressConferenceExtractor):
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

        reporter = PressConferenceReporter(extraction_version=EXTRACTION_VERSION)
        state = _RunState()

        for index, section in enumerate(document.sections):
            self._walk_lines(result, document, index, section.text or "", reporter, state)

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
        current_speaker: str | None = None
        turn = state.turn_counter

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            if _ALL_CAPS_LABEL_RE.match(line):
                label = _label_of(line)
                if _is_fed_label(line):
                    # A Fed-official label. Remarks until the first journalist
                    # label; afterwards an answer of the current Q&A turn.
                    current_speaker = label
                    state.last_fed_label = label
                    if state.seen_journalist:
                        state.mode = "qna"
                    turn = state.turn_counter
                else:
                    # Journalist / moderator label: new Q&A turn, never mined.
                    state.seen_journalist = True
                    state.mode = "qna"
                    state.turn_counter += 1
                    turn = state.turn_counter
                    current_speaker = None
                continue

            # Plain content line.
            if current_speaker is None:
                continue  # journalist question continuation → never mined
            if state.mode == "qna" and turn == 0:
                # A Fed label with no journalist turn yet would have set remarks;
                # an unprefixed line before any turn is unattributed → skip.
                continue
            context = "remarks" if state.mode == "remarks" else f"answer:{turn}"
            speaker = None if state.mode == "remarks" else current_speaker
            if state.mode == "remarks":
                state.remarks_content = True
            else:
                state.qna_content = True
            self._mine_content(result, document, index, line, reporter, state, speaker=speaker, context=context)

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
        if self._matches(RISK_ANCHORS, sentence):
            return CAT_RISK
        if self._matches(FINANCIAL_ANCHORS, sentence):
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
        if _POLICY_STANCE_PHRASE.search(sentence):
            return True
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
        elif "core inflation" in lower or "core pce" in lower:
            subject = SUBJECT_CORE_INFLATION
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