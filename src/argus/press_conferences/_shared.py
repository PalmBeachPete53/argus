"""Structural helpers shared by the bank-specific Press Conference extractors
(Phase 4.x).

These helpers are deliberately **structural only**: canonical subject/predicate
vocabulary, normalized-heading cleaning, box detection and sentence splitting,
numeric value-claim parsing, the explicit-value gate, the qualitative-assertion
gate, the generic English economic anchor sets, and a deterministic,
provenance-carrying fact ``PressConferenceReporter``.

They carry **no bank-specific semantics**: every bank's press-conference layout
(remarks vs Q&A routing, transcript labels, speaker detection, guidance/policy
vernacular) lives in that bank's own module (``press_conferences/{bank}.py``).
Nothing here knows about any particular central bank's transcript structure or
wording, and there is no ``if bank == "…":`` dispatch. Reusing these helpers
across banks is the "genuinely structural operations" the phase contract
allows; encoding bank wording here is forbidden.

The canonical Phase 4.3 subjects are reused verbatim — no subject is added for a
press conference family. The ``PressConferenceReporter`` carries the Phase 4.3
attribution contract deterministically: ``identity_qualifier`` is
``remarks:{n}`` for a collective remarks fact and ``answer:{turn}:{n}`` for an
individual Q&A answer fact (``turn`` = 1-based Q&A turn, ``n`` = per-turn
ordinal). ``Fact.speaker`` is supplied explicitly by the bank-specific layer
(verbatim official label when the source states one, ``None`` otherwise) and is
never invented here.
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

# Canonical Phase 4.3 press conference subjects (controlled vocabulary, the same
# set the ECB reference extractor uses — reused verbatim).
SUBJECT_INFLATION = "inflation"
SUBJECT_CORE_INFLATION = "core_inflation"
SUBJECT_INFLATION_EXPECTATIONS = "inflation_expectations"
SUBJECT_INFLATION_DRIVER = "inflation_driver"
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
# Explicit value claims (structural). A percentage becomes a Fact only behind
# an explicit value-claim verb; a value with no reference period is kept
# without one except for a *forecast*, which is under-determined and ignored;
# share/ratio units ("% of GDP") are never converted into percentage facts;
# a GDP deflator / per-capita mention is never a GDP value.
# ---------------------------------------------------------------------------
_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"
_MONTH_NUM = {
    "january": "01", "february": "02", "march": "03", "april": "04", "may": "05", "june": "06",
    "july": "07", "august": "08", "september": "09", "october": "10", "november": "11", "december": "12",
}
_QUARTER_NUM = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}
VALUE_TOKEN = rf"{_RATE_ITEM}\s*(?:%|per\s+cent|percent)"

_VALUE_GATE = re.compile(
    r"(?:projected|expected|forecast)\s*(?:to\s+)?(?:average|stand\s+at|be|reach|amount\s+to|"
    r"grow\s+by|grow\s+from|expand\s+by|expand\s+from|increase\s+(?:by|from|to)|rise\s+(?:by|from|to)|"
    r"decline\s+to|decline\s+from|fall\s+to|fall\s+from|remain\s+at)\s+"
    r"|(?:stood at|standing at|averaged|was at|were at|is at|are at|stands at|running at|remain(?:s|ed|ing)? at|"
    r"declined to|declined by|declined from|fell to|fell by|fell from|dropped to|dropped by|dropped from|"
    r"rose to|rose by|rose from|increased to|increased by|increased from|"
    r"expanded by|grew by|contracted by|narrowed to|widened to|reached)\s+"
    r"|(?:was|were|is|are|stood|stands|standing|averaged|running|remain(?:s|ed|ing)?)\s+"
    r"(?:at\s+)?(?:about|around|roughly|approximately)\s+",
    re.IGNORECASE,
)
_FORECAST_VERB = re.compile(r"\b(?:projected|expected|forecast)\b", re.IGNORECASE)
_PERCENT_WITH_QUARTER = re.compile(
    rf"(?P<token>{VALUE_TOKEN})\s+(?P<period>in|during)\s+(?:the\s+)?"
    rf"(?P<quarter>first|second|third|fourth)\s+quarter\s+of\s+(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_PERCENT_WITH_MONTH = re.compile(
    rf"(?P<token>{VALUE_TOKEN})\s+(?P<period>in|during)\s+(?P<month>{_MONTH_WORDS})\s+(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_PERCENT_WITH_YEAR = re.compile(
    rf"(?P<token>{VALUE_TOKEN})\s+(?P<period>in|during|by|for)\s+(?:the\s+)?(?P<year>20[0-9]{{2}})\b",
    re.IGNORECASE,
)
_VALUE_TOKEN_ONLY = re.compile(rf"(?P<token>{VALUE_TOKEN})", re.IGNORECASE)
_SHARE_UNIT = re.compile(
    r"\bof\s+(?:gdp|gross\s+domestic\s+product|total|disposable\s+income|labour\s+(?:force|income))\b",
    re.IGNORECASE,
)
GDP_NEAR_MISS = re.compile(
    r"(?:\bgdp\b\s+(?:deflator|per\s+capita)\b|per\s+capita\s+\bgdp\b)",
    re.IGNORECASE,
)


def token_value(token: str) -> float:
    return float(re.match(r"[0-9.]+", token).group(0))


def is_share(sentence: str, match: re.Match) -> bool:
    window = sentence[match.end("token") : match.end("token") + 80]
    return bool(_SHARE_UNIT.search(window))


def period_for(match: re.Match) -> FactPeriod | None:
    gd = match.groupdict()
    if "year" not in gd or not gd.get("year"):
        return None
    year = gd["year"]
    if "quarter" in gd and gd.get("quarter"):
        return FactPeriod(PeriodKind.QUARTER, f"{year}-{_QUARTER_NUM[gd['quarter'].lower()]}", label=sentence_label(match))
    if "month" in gd and gd.get("month"):
        return FactPeriod(PeriodKind.MONTH, f"{year}-{_MONTH_NUM[gd['month'].lower()]}", label=sentence_label(match))
    return FactPeriod(PeriodKind.YEAR, year, label=sentence_label(match))


def sentence_label(match: re.Match) -> str:
    gd = match.groupdict()
    start = match.start("period") if gd.get("period") else match.start("token")
    end = match.end()
    return match.string[start:end]


# ---------------------------------------------------------------------------
# Qualitative fact gate (structural) — Phase 4.7-style hardening. A qualitative
# assessment (financial / inflation / labour / growth verbatim text) is only
# emitted when the sentence states an explicit economic assertion; economic
# vocabulary alone is never enough. The gate is layered:
#
#   1. _ASSERTION_SIGNAL — a change/state verb or forecast construction exists;
#   2. _PLATITUDE        — copular rhetoric ("X is important") is rejected;
#   3. _TRANSITIVE_ABUSE — a change verb acting on a possessive object
#                          ("tightened our procedures") is rejected;
#   4. _POSSESSOR_ABUSE  — a change verb whose subject is an "of <anchor>"
#                          possessor ("our understanding of the economy
#                          improved") is rejected.
#
# Guidance / policy statements (already precise compound signals) and value
# claims / categorical risk orientations pass regardless.
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
# Generic English economic anchor sets (structural — no bank identity). Banks
# rely on these for the content-first sentence classification (precedence:
# guidance > policy > risk > financial > inflation > labour > growth) and add
# their own vocabulary (headings, guidance/policy anchors, inflation terms) in
# their own module. Risk orientations are never inferred from these anchors.
# ---------------------------------------------------------------------------
RISK_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\brisks?\s+(?:to|for|around|surrounding|from|of|are|were|remain|remained|have|has)\b", re.IGNORECASE),
    re.compile(r"\b(?:downside|upside|two-sided|symmetric|broadly\s+balanced)\s+risks?\b", re.IGNORECASE),
    re.compile(r"\buncertain(?:ty|ties)?\b", re.IGNORECASE),
    re.compile(r"\btilted\b", re.IGNORECASE),
)

FINANCIAL_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bfinancial conditions\b", re.IGNORECASE),
    re.compile(r"\bfinancing conditions?\b", re.IGNORECASE),
    re.compile(r"\bcredit growth\b", re.IGNORECASE),
    re.compile(r"\bcredit standards?\b", re.IGNORECASE),
    re.compile(r"\bbank lending\b", re.IGNORECASE),
    re.compile(r"\blending rates?\b", re.IGNORECASE),
    re.compile(r"\bspreads?\b", re.IGNORECASE),
    re.compile(r"\btransmission\b", re.IGNORECASE),
    re.compile(r"\bmarket rates?\b", re.IGNORECASE),
    re.compile(r"\bborrowing costs?\b", re.IGNORECASE),
    re.compile(r"\bfunding\b", re.IGNORECASE),
    re.compile(r"\bfinancial stability\b", re.IGNORECASE),
)

INFLATION_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\binflation\b", re.IGNORECASE),
    re.compile(r"\bdeflation\b", re.IGNORECASE),
    re.compile(r"\bprice pressures\b", re.IGNORECASE),
    re.compile(r"\bconsumer prices?\b", re.IGNORECASE),
)

LABOUR_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\blabour market\b", re.IGNORECASE),
    re.compile(r"\blabor market\b", re.IGNORECASE),
    re.compile(r"\bunemployment\b", re.IGNORECASE),
    re.compile(r"\bemployment\b", re.IGNORECASE),
    re.compile(r"\bwage\b", re.IGNORECASE),
    re.compile(r"\bwages\b", re.IGNORECASE),
)

GROWTH_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bgrowth\b", re.IGNORECASE),
    re.compile(r"\bgdp\b", re.IGNORECASE),
    re.compile(r"\bactivity\b", re.IGNORECASE),
    re.compile(r"\beconom(?:y|ic)\b", re.IGNORECASE),
    re.compile(r"\boutput\b", re.IGNORECASE),
    re.compile(r"\bdomestic demand\b", re.IGNORECASE),
    re.compile(r"\baggregate demand\b", re.IGNORECASE),
    re.compile(r"\bconsumer spending\b", re.IGNORECASE),
    re.compile(r"\binvestment\b", re.IGNORECASE),
)


class PressConferenceReporter:
    """Deterministic, provenance-carrying fact emitter for press conferences.

    One instance per extractor run. ``extraction_version`` is the structural
    configuration supplied by the bank-specific extractor. Every emitted Fact
    carries ``identity_qualifier`` according to the Phase 4.3 attribution
    contract: ``remarks:{n}`` for a remarks fact and ``answer:{turn}:{n}`` for
    a Q&A answer fact (the ``context`` is supplied per emission by the bank
    extractor as ``"remarks"`` or ``f"answer:{turn}"``). ``Fact.speaker`` is
    provided by the bank extractor (verbatim label when the source states one,
    ``None`` otherwise). Within-run deduplication emits the same assertion once.
    """

    def __init__(self, *, extraction_version: str) -> None:
        self.version = extraction_version
        self.counters: dict[str, int] = {}
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
        context: str = "",
        location: FactLocation | None = None,
        method: str = METHOD_REGEX,
    ) -> Fact | None:
        if self._dedup_key(subject, predicate, period, value, source_text) in self.seen:
            return None
        self.seen.add(self._dedup_key(subject, predicate, period, value, source_text))

        ordinal = self.counters.get(context, 0)
        self.counters[context] = ordinal + 1
        qualifier = f"{context}:{ordinal}" if context else ""

        loc = location if location is not None else FactLocation(LocationKind.SECTION, section=index)
        return Fact(
            publication_id=result.publication_id,
            document_id=document.document_id,
            subject=subject,
            predicate=predicate,
            value=value,
            period=period,
            effective_date=None,
            source_location=loc,
            source_text=source_text,
            extraction_method=method,
            extraction_version=self.version,
            confidence=confidence,
            speaker=speaker,
            identity_qualifier=qualifier,
        )

    def emit_text(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        subject: str,
        predicate: str,
        *,
        speaker: str | None = None,
        context: str = "",
    ) -> None:
        fact = self.emit(
            result, document, index, sentence, subject, predicate,
            FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
            Confidence.MEDIUM, speaker=speaker, context=context,
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
        *,
        speaker: str | None = None,
        context: str = "",
    ) -> None:
        """Risk sentence: categorical orientation when explicit, else verbatim."""
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
                speaker=speaker, context=context,
            )
        else:
            fact = self.emit(
                result, document, index, sentence, subject, PREDICATE_ASSESSMENT,
                FactValue(ValueKind.TEXT, value=sentence, source_text=sentence), Confidence.MEDIUM,
                speaker=speaker, context=context,
            )
        if fact is not None:
            result.add(fact)

    def emit_value_facts(
        self,
        result: ExtractionResult,
        document: NormalizedDocument,
        index: int,
        sentence: str,
        subject: str,
        *,
        speaker: str | None = None,
        context: str = "",
    ) -> int:
        """Emit percentage value facts for explicit value claims.

        A percentage with an explicit reference period (year / month / quarter
        from the wording) keeps that period; a percentage without one is kept
        without a period unless it is a *forecast*, which is under-determined
        and ignored. Share/ratio units ("% of GDP") are never percentages.
        Returns the number of facts emitted (nonzero also when a genuine claim
        was present but deduped/ignored, so the caller keeps the sentence).
        """
        if not _VALUE_GATE.search(sentence):
            return 0
        if subject == SUBJECT_GDP and GDP_NEAR_MISS.search(sentence):
            return 0
        forecast = bool(_FORECAST_VERB.search(sentence))
        covered: list[tuple[int, int]] = []
        emitted = 0
        claimed = False

        for finder in (_PERCENT_WITH_QUARTER, _PERCENT_WITH_MONTH, _PERCENT_WITH_YEAR, _VALUE_TOKEN_ONLY):
            for match in finder.finditer(sentence):
                if any(start <= match.start("token") and match.end("token") <= end for start, end in covered):
                    continue
                if is_share(sentence, match):
                    continue
                claimed = True
                covered.append((match.start("token"), match.end("token")))
                period = period_for(match)
                if period is None and forecast:
                    continue  # a forecast without a reference period is ignored
                token = match.group("token")
                fact = self.emit(
                    result, document, index, sentence, subject, PREDICATE_VALUE,
                    percentage(token_value(token), source_text=token), Confidence.HIGH,
                    period=period, speaker=speaker, context=context,
                )
                if fact is not None:
                    result.add(fact)
                    emitted += 1
        return emitted if emitted else (1 if claimed else 0)


__all__ = [
    "SUBJECT_INFLATION",
    "SUBJECT_CORE_INFLATION",
    "SUBJECT_INFLATION_EXPECTATIONS",
    "SUBJECT_INFLATION_DRIVER",
    "SUBJECT_GROWTH",
    "SUBJECT_GDP",
    "SUBJECT_LABOUR_MARKET",
    "SUBJECT_UNEMPLOYMENT",
    "SUBJECT_WAGES",
    "SUBJECT_FINANCIAL_CONDITIONS",
    "SUBJECT_RISK",
    "SUBJECT_INFLATION_RISK",
    "SUBJECT_GROWTH_RISK",
    "SUBJECT_MONETARY_POLICY",
    "SUBJECT_POLICY_GUIDANCE",
    "PREDICATE_ASSESSMENT",
    "PREDICATE_STATEMENT",
    "PREDICATE_VALUE",
    "clean_heading",
    "is_box",
    "split_sentences",
    "token_value",
    "is_share",
    "period_for",
    "sentence_label",
    "is_economic_assertion",
    "RISK_ANCHORS",
    "FINANCIAL_ANCHORS",
    "INFLATION_ANCHORS",
    "LABOUR_ANCHORS",
    "GROWTH_ANCHORS",
    "GDP_NEAR_MISS",
    "PressConferenceReporter",
]