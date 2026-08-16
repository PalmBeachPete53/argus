"""Structural helpers shared by the bank-specific Speech extractors (Phase 4.x).

These helpers are deliberately **structural only**: canonical subject/predicate
vocabulary, normalized-heading cleaning, analytical-box detection and sentence
splitting, explicit-speaker / metadata-author detection, quotation detection,
the qualitative-assertion gate, explicit numeric value-claim parsing and a
deterministic, provenance-carrying fact ``Reporter`` with within-run
deduplication.

They carry **no bank-specific semantics**: every bank's heading vocabulary
(`_IGNORE_HEADINGS` / economic headings), content anchors, section routing
(`_section_category`), subject resolution and speaker conventions live in that
bank's own module (`speeches/{bank}.py`). Nothing here knows about any
particular central bank's speech layout, wording or terminology, and there is
no ``if bank == "…":`` dispatch. Reusing these helpers across banks is the
"genuinely structural operations" the phase contract allows; encoding bank
wording here is forbidden.
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

# Canonical Phase 4.x/11 Speech subjects (controlled vocabulary). These reuse
# the Phase 4.2/7/8/10 subjects verbatim — no subject is added for speeches.
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
# heading normalization (structural) — case, numbering, footnote marks, leading
# "the", trailing punctuation, collapsed whitespace. Nothing else.
# ---------------------------------------------------------------------------
_FOOTNOTE_MARK = re.compile(r"\s*(?:\(\d+\)|\[\d+\]|\d+\)|[*†‡]+)\s*$")
_BOX_PREFIX = re.compile(r"^\s*box\b", re.IGNORECASE)
_LEADING_NUM = re.compile(r"^\s*(?:[0-9]+(?:\.[0-9]+)*)\s*[-–—.:]?\s*")
_LEADING_THE = re.compile(r"^the\s+")
_TRAILING_PUNCT = re.compile(r"[\s.:;,\-–—]+$")


def clean_heading(heading: str) -> str:
    """Normalize a section heading: collapse case, strip leading numbering,
    trailing footnote marks, leading "the" and trailing punctuation."""
    t = normalize_title(heading or "")
    if not t:
        return ""
    t = _LEADING_NUM.sub("", t).strip()
    t = _FOOTNOTE_MARK.sub("", t).strip()
    t = _LEADING_THE.sub("", t).strip()
    return _TRAILING_PUNCT.sub("", t).strip()


def is_box(heading: str) -> bool:
    """True when a (normalized) heading is an analytical box — never mined."""
    return bool(_BOX_PREFIX.match(heading or ""))


def split_sentences(text: str) -> list[str]:
    """Split paragraph text into non-empty sentences, each verbatim (trailing
    period preserved). Both ``". "`` and ``".\\n"`` are boundaries."""
    return [part.strip() for part in re.split(r"(?<=\.)\s+", text or "") if part.strip()]


# ---------------------------------------------------------------------------
# Speaker attribution — explicit only. A ``Speaker: <label>`` line in the body
# wins over an explicit author field in the document metadata; when neither is
# present the speaker is ``None`` (never inferred). The label is preserved
# verbatim in ``Fact.speaker``.
# ---------------------------------------------------------------------------
_SPEAKER_LINE = re.compile(r"^\s*speaker\s*[:\-–]\s*(?P<speaker>.+?)\s*$", re.IGNORECASE)

_META_AUTHOR_KEYS = ("author", "dc.creator")


def speaker_from_document(document: NormalizedDocument) -> str | None:
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


def is_quoted_other(sentence: str, speaker_label: str | None) -> bool:
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
# Qualitative fact gate — Phase 4.7 hardening. A qualitative assessment is only
# emitted when the sentence states an explicit economic assertion; economic
# vocabulary alone ("the economy", "credit", "investment", "growth") is never
# enough. The gate is layered:
#
#   1. _ASSERTION_SIGNAL  — a change/state verb or forecast construction exists;
#   2. _PLATITUDE         — copular rhetoric ("X is important", "X remains a
#                           priority") is always rejected;
#   3. _TRANSITIVE_ABUSE  — a change verb applied to a possessive object
#                           ("improved our understanding") describes an
#                           institutional action, not an economic state;
#   4. _POSSESSOR_ABUSE   — a change verb whose subject is an "of <anchor>"
#                           possessor ("our understanding of the economy
#                           improved") describes the possessor.
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
    r"(?:(?:is|are|was|were|be|been|being|remains?|remained|stays?|stayed|"
    r"continue(?:s|d)?\s+to\s+be|remain(?:s|ed)?\s+to\s+be|stay(?:s|ed)?\s+to\s+be)\s+)"
    r"(?:"
    r"(?:important|essential|central|critical|crucial|vital|fundamental|necessary|key|strategic|significant|relevant|primary)"
    r"|(?:a|an|the|our)\s+(?:(?:key|crucial|central|important|vital|essential|strategic|fundamental|significant|integral|core|primary)\s+)?"
    r"(?:priority|challenge|matter|goal|mandate|objective|concern|issue|aim|focus|task|responsibility|consideration|part)"
    r"|part\s+of\b"
    r"|at\s+the\s+(?:heart|centre|center|core)\s+of"
    r"|(?:central|important|essential|crucial|vital|key|fundamental)\s+to"
    r")"
    r"|matter(?:s|ed)?"
    r")\b",
    re.IGNORECASE,
)

_TRANSITIVE_ABUSE = re.compile(
    r"\b(?:"
    r"improv(?:ed|es|e|ing)|recover(?:ed|ing)?|expand(?:ed|ing)?|narrow(?:ed|ing)?|widen(?:ed|ing)?|"
    r"ease(?:d|ing)?|tighten(?:ed|ing)?|loosen(?:ed|ing)?|strengthen(?:ed|ing)?|weaken(?:ed|ing)?|"
    r"accelerat(?:ed|es|e|ing)|decelerat(?:ed|es|e|ing)|increas(?:ed|es|e|ing)|decreas(?:ed|es|e|ing)|"
    r"declin(?:ed|es|e|ing)|deteriorat(?:ed|es|e|ing)|slow(?:ed|ing)?|rebound(?:ed|ing)?|surge(?:d|s|ing)?|"
    r"normalis(?:ed|ing|es|e)?|normaliz(?:ed|ing|es|e)?|broaden(?:ed|ing)?|pick(?:s|ed)?\s+up"
    r")\s+(?:our|their|its|his|her)\s+\w+",
    re.IGNORECASE,
)

_POSSESSOR_ABUSE = re.compile(
    r"\bof\s+(?:the|our|their|its|his|her|this|that|a|an)?\s*\w+\s+"
    r"(?:improv(?:ed|es|e|ing)|recover(?:ed|ing)?|expand(?:ed|ing)?|narrow(?:ed|ing)?|widen(?:ed|ing)?|"
    r"ease(?:d|ing)?|tighten(?:ed|ing)?|loosen(?:ed|ing)?|strengthen(?:ed|ing)?|weaken(?:ed|ing)?|"
    r"accelerat(?:ed|es|e|ing)|decelerat(?:ed|es|e|ing)|increas(?:ed|es|e|ing)|decreas(?:ed|es|e|ing)|"
    r"declin(?:ed|es|e|ing)|deteriorat(?:ed|es|e|ing)|slow(?:ed|ing)?|rebound(?:ed|ing)?|surge(?:d|s|ing)?|"
    r"normalis(?:ed|ing|es|e)?|normaliz(?:ed|ing|es|e)?|broaden(?:ed|ing)?"
    r")\b",
    re.IGNORECASE,
)


def is_economic_assertion(sentence: str) -> bool:
    """True when the sentence makes an explicit economic assertion rather than
    merely mentioning a topic rhetorically."""
    if not _ASSERTION_SIGNAL.search(sentence):
        return False
    if _PLATITUDE.search(sentence):
        return False
    if _TRANSITIVE_ABUSE.search(sentence):
        return False
    if _POSSESSOR_ABUSE.search(sentence):
        return False
    return True


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


def token_value(token: str) -> float:
    return float(re.match(r"[0-9.]+", token).group(0))


def is_share(sentence: str, match: re.Match) -> bool:
    """True when the value token is followed by a share/ratio unit ("% of
    GDP", "% of total", …) — such ratios are never stored as percentages."""
    window = sentence[match.end("token") : match.end("token") + 80]
    return bool(_SHARE_UNIT.search(window))


class Reporter:
    """Deterministic, provenance-carrying fact emitter with within-run dedup.

    One instance per extractor run. ``extraction_version`` is the structural
    configuration supplied by the bank-specific extractor; the identity
    qualifier prefix is family-scoped (``speech:``) so ids stay unique.
    """

    def __init__(self, *, extraction_version: str) -> None:
        self.version = extraction_version
        self.counters: dict[tuple, int] = {}
        self.seen: set[tuple] = set()

    @staticmethod
    def _dedup_key(subject: str, predicate: str, period: FactPeriod | None, value: FactValue, source_text: str) -> tuple:
        period_key = period.canonical() if period else ""
        if value.kind in (ValueKind.NUMBER, ValueKind.PERCENTAGE, ValueKind.BASIS_POINTS, ValueKind.CURRENCY):
            return (subject, predicate, period_key, value.value)
        return (subject, predicate, period_key, normalize_title(source_text or ""))

    def emit(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        source_text: str,
        subject: str,
        predicate: str,
        value: FactValue,
        confidence: Confidence,
        *,
        period: FactPeriod | None = None,
        speaker: str | None = None,
    ) -> Fact | None:
        """Build a Fact with a deterministic ordinal qualifier, suppressing
        within-run duplicates. A quantitative duplicate is defined by subject +
        predicate + period + value; a qualitative one by subject + predicate +
        period + normalized verbatim wording."""
        period_key = period.canonical() if period else ""
        if self._dedup_key(subject, predicate, period, value, source_text) in self.seen:
            return None
        self.seen.add(self._dedup_key(subject, predicate, period, value, source_text))

        key = (subject, predicate, period_key)
        ordinal = self.counters.get(key, 0)
        self.counters[key] = ordinal + 1

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
            extraction_version=self.version,
            confidence=confidence,
            speaker=speaker,
            identity_qualifier=f"speech:{subject}:{ordinal}",
        )

    def emit_text(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        subject: str,
        predicate: str,
        speaker: str | None,
    ) -> None:
        fact = self.emit(
            result, document, index, sentence, subject, predicate,
            FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
            Confidence.MEDIUM, speaker=speaker,
        )
        if fact is not None:
            result.add(fact)

    def emit_risk(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        subject: str,
        speaker: str | None,
        *,
        strict: bool,
    ) -> None:
        """A risk sentence yields a categorical orientation fact when an
        explicit orientation word is present; otherwise a verbatim text
        assessment (unknown sections keep only the categorical orientation —
        a bare risk mention is never an automatic fact)."""
        lower = sentence.lower()
        orientation = None
        if re.search(r"\b(?:broadly\s+)?balanced\b|\btwo-sided\b|\bsymmetric\b", lower):
            orientation = "balanced"
        elif re.search(r"\bdownside\b", lower):
            orientation = "downside"
        elif re.search(r"\bupside\b", lower):
            orientation = "upside"

        if orientation is not None:
            fact = self.emit(
                result, document, index, sentence, subject, PREDICATE_ASSESSMENT,
                categorical(orientation, source_text=sentence), Confidence.HIGH,
                speaker=speaker,
            )
        elif not strict and is_economic_assertion(sentence):
            fact = self.emit(
                result, document, index, sentence, subject, PREDICATE_ASSESSMENT,
                FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
                Confidence.MEDIUM, speaker=speaker,
            )
        else:
            fact = None
        if fact is not None:
            result.add(fact)

    def emit_value_facts(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        subject: str,
        speaker: str | None,
    ) -> int:
        """Emit ``subject/value`` percentage facts for explicit value claims.

        A percentage followed by an explicit reference period (year, month or
        quarter) keeps it as ``FactPeriod``; a percentage with no stated period
        is kept without one — except for *forecasts*, which are under-determined
        without a reference period and are therefore ignored. Share/ratio units
        ("% of GDP") are never converted into percentage facts. Returns the
        number of facts emitted (nonzero also when a genuine claim was present
        but deduped/ignored, so the caller keeps the sentence).
        """
        if not _VALUE_GATE.search(sentence):
            return 0

        forecast = bool(_FORECAST_VERB.search(sentence))
        covered: list[tuple[int, int]] = []
        emitted = 0
        claimed = False  # a genuine value claim was present (even if deduped or ignored)

        for match in _PERCENT_WITH_QUARTER.finditer(sentence):
            token = match.group("token")
            if is_share(sentence, match):
                continue
            claimed = True
            period = FactPeriod(
                PeriodKind.QUARTER,
                f"{match.group('year')}-{_QUARTER_NUM[match.group('quarter').lower()]}",
                label=sentence[match.start("period") : match.end("year")],
            )
            covered.append((match.start("token"), match.end("token")))
            fact = self.emit(
                result, document, index, sentence, subject, PREDICATE_VALUE,
                percentage(token_value(token), source_text=token),
                Confidence.HIGH, period=period, speaker=speaker,
            )
            if fact is not None:
                result.add(fact)
                emitted += 1

        for match in _PERCENT_WITH_MONTH.finditer(sentence):
            token = match.group("token")
            if is_share(sentence, match):
                continue
            claimed = True
            period = FactPeriod(
                PeriodKind.MONTH,
                f"{match.group('year')}-{_MONTH_NUM[match.group('month').lower()]}",
                label=sentence[match.start("period") : match.end("year")],
            )
            covered.append((match.start("token"), match.end("token")))
            fact = self.emit(
                result, document, index, sentence, subject, PREDICATE_VALUE,
                percentage(token_value(token), source_text=token),
                Confidence.HIGH, period=period, speaker=speaker,
            )
            if fact is not None:
                result.add(fact)
                emitted += 1

        for match in _PERCENT_WITH_YEAR.finditer(sentence):
            token = match.group("token")
            if is_share(sentence, match):
                continue
            claimed = True
            period = FactPeriod(
                PeriodKind.YEAR,
                match.group("year"),
                label=sentence[match.start("period") : match.end("year")],
            )
            covered.append((match.start("token"), match.end("token")))
            fact = self.emit(
                result, document, index, sentence, subject, PREDICATE_VALUE,
                percentage(token_value(token), source_text=token),
                Confidence.HIGH, period=period, speaker=speaker,
            )
            if fact is not None:
                result.add(fact)
                emitted += 1

        for match in _VALUE_TOKEN_ONLY.finditer(sentence):
            if any(start <= match.start("token") and match.end("token") <= end for start, end in covered):
                continue
            if is_share(sentence, match):
                continue
            claimed = True
            if forecast:
                continue  # a forecast without an explicit reference period is ignored
            token = match.group("token")
            fact = self.emit(
                result, document, index, sentence, subject, PREDICATE_VALUE,
                percentage(token_value(token), source_text=token),
                Confidence.HIGH, speaker=speaker,
            )
            if fact is not None:
                result.add(fact)
                emitted += 1

        return emitted if emitted else claimed


__all__ = [
    "SUBJECT_INFLATION", "SUBJECT_CORE_INFLATION", "SUBJECT_INFLATION_EXPECTATIONS",
    "SUBJECT_GROWTH", "SUBJECT_GDP", "SUBJECT_LABOUR_MARKET", "SUBJECT_UNEMPLOYMENT",
    "SUBJECT_WAGES", "SUBJECT_FINANCIAL_CONDITIONS", "SUBJECT_RISK",
    "SUBJECT_INFLATION_RISK", "SUBJECT_GROWTH_RISK", "SUBJECT_MONETARY_POLICY",
    "SUBJECT_POLICY_GUIDANCE",
    "PREDICATE_ASSESSMENT", "PREDICATE_STATEMENT", "PREDICATE_VALUE",
    "clean_heading", "is_box", "split_sentences",
    "speaker_from_document", "is_quoted_other", "is_economic_assertion",
    "token_value", "is_share", "Reporter",
]
