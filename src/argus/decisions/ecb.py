"""ECB — monetary policy decision extractor.

Extracts the facts of an ECB monetary policy decision from the normalized
document text, answering "what did the Governing Council explicitly decide or
announce as part of the decision?":

- the decision date (the leading date paragraph, per ECB layout; the decision
  section is the structural fallback — never an arbitrary date anywhere)
- the decision wording (the source's "… decided to …" sentences, verbatim)
- the three key ECB interest rates:
  ``deposit_facility_rate``, ``main_refinancing_rate``, ``marginal_lending_rate``
- explicit rate changes (direction + magnitude, e.g. "lowered by 25 basis
  points") — sign preserved as basis points (negative = easing)
- the effective date when explicitly stated ("with effect from …")
- asset-purchase / balance-sheet decisions (APP, PEPP, TLTRO) where the source
  states an explicit action (reinvestment, cessation, continuation, maturity
  handling) — programme identity kept as ``identity_qualifier``
- explicit forward guidance as part of the decision, verbatim (never classified
  hawkish/dovish, never interpreted)

Deliberately NOT extracted (Phase 4.1 boundary):

- votes — ECB Monetary Policy Decisions do not report individual votes; the
  extractor never fabricates a ``vote`` fact
- a full risk assessment — the decision document carries none; risk language
  belongs to the separate Monetary Policy Statement (Phase 4.2)
- macro-economic justification (inflation/growth/employment analysis) — Phase 4.2

Design rules

- No fact is invented. A value/delta/date is only produced when the source
  states it, and every Fact preserves an *exact verbatim* supporting passage
  (``source_text``) and value wording (``FactValue.source_text``) copied from
  the normalized document — never a reconstructed string.
- Rate identification is explicit. The combined "… to A%, B% and C%
  respectively" sentence names its instruments in order; the extractor reads
  those names. When the enumeration cannot be mapped to all three rates
  reliably, it emits a warning and extracts nothing rather than assuming a
  canonical order.
- Confidence is ``HIGH`` for every fact: each produced fact is deterministically
  identified from explicit source wording. Confidence is never lowered merely
  because a fallback scan path was used.
- A hold ("unchanged", "remain at …") produces level facts only — never a
  ``change`` fact (no invented 0 bp), unless the source states an explicit
  zero delta.
- Absence of an optional statement (no APP/PEPP section, no guidance, no vote
  wording) never becomes an invented "no change" or "no action" fact.
"""

from __future__ import annotations

import re
from datetime import datetime

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
    basis_points,
    date_value,
    percentage,
)
from ..normalize import normalize_title, parse_datetime
from .base import DecisionExtractor

EXTRACTION_VERSION = "5.2.0"

# ---------------------------------------------------------------------------
# Canonical Phase 4.1 subjects (controlled vocabulary, see docs/EXTRACTORS.md).
# ---------------------------------------------------------------------------
SUBJECT_DECISION = "monetary_policy_decision"
SUBJECT_DEPOSIT_FACILITY = "deposit_facility_rate"
SUBJECT_MAIN_REFINANCING = "main_refinancing_rate"
SUBJECT_MARGINAL_LENDING = "marginal_lending_rate"
SUBJECT_ASSET_PURCHASE = "asset_purchase"
SUBJECT_POLICY_GUIDANCE = "policy_guidance"

PREDICATE_DATE = "date"
PREDICATE_VALUE = "value"
PREDICATE_CHANGE = "change"
PREDICATE_STATEMENT = "statement"
PREDICATE_DECISION = "decision"

# Spanish-style "escalated" variants are intentionally absent; ECB uses the
# canonical name. Instrument phrase → canonical subject, in canonical order.
_INSTRUMENT_SUBJECTS: tuple[tuple[str, str], ...] = (
    ("main refinancing operations", SUBJECT_MAIN_REFINANCING),
    ("marginal lending facility", SUBJECT_MARGINAL_LENDING),
    ("deposit facility", SUBJECT_DEPOSIT_FACILITY),
)
_CANONICAL_ORDER = [s for _, s in _INSTRUMENT_SUBJECTS]

# Directional verbs used to read the sign of an explicit change. Non-directional
# verbs express a *level* ("keep/maintain/hold the rate at …") and are grouped
# separately so a hold never fabricates a delta.
_DIRECTIONAL_VERBS = r"(?:lower|decrease|reduce|cut|drop|ease|increase|raise|hike|lift)"
_LEVEL_VERBS = r"(?:lower|decrease|reduce|cut|drop|ease|increase|raise|hike|lift|set|keep|maintain|hold|leave)"
_RATE_ITEM = r"[0-9]+(?:\.[0-9]+)?"
_MONTH_WORDS = "January|February|March|April|May|June|July|August|September|October|November|December"

_DATE_TOKEN = re.compile(
    rf"\b[0-9]{{1,2}}\s+({_MONTH_WORDS})\s+[0-9]{{4}}|\b[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}\b",
    re.IGNORECASE,
)
# A single percentage value token, verbatim, e.g. "2.00%" or "1.75 per cent".
_VALUE_TOKEN = rf"{_RATE_ITEM}\s*(?:%|per\s+cent)"

# Combined ECB enumeration sentence:
#   "… will be decreased to 2.00%, 2.25% and 1.75% respectively"
# or for a hold:
#   "… remain at 2.00%, 2.25% and 1.75% respectively".
# Each group keeps the verbatim token (number + unit) so provenance is exact.
_RATES_ENUMERATED = re.compile(
    r"(?:lowered|decreased|increased|raised|reduced|cut|hiked|set|kept|maintained|held|left|remain|stayed)"
    r"\s+(?:to|at)\s+"
    rf"(?P<a>{_RATE_ITEM}%)\s*,\s*(?P<b>{_RATE_ITEM}%)\s+and\s+(?P<c>{_RATE_ITEM}%)\s+respectively",
    re.IGNORECASE,
)
_EFFECTIVE_DATE = re.compile(
    r"(?:with\s+effect\s+from|effective\s+(?:from|as\s+of)|as\s+of)\s+"
    rf"(?P<date>[0-9]{{1,2}}\s+[A-Za-z]+\s+[0-9]{{4}}|\d{{4}}-\d{{2}}-\d{{2}})",
    re.IGNORECASE,
)
_AMOUNT_BY = re.compile(rf"\bby\s+(?P<amount>{_RATE_ITEM})\s+basis\s+points?", re.IGNORECASE)
_BROAD_CONTEXT = re.compile(
    r"\b(?:the\s+)?three\s+key\b|\bkey\s+ecb\s+interest\s+rates?\b|\bkey\s+interest\s+rates?\b|\ball\s+three\b",
    re.IGNORECASE,
)
_DIRECTION_MARKERS: tuple[tuple[re.Pattern, int], ...] = (
    (re.compile(rf"\b(?:lower|decrease|reduce|cut|drop|ease)\w*", re.IGNORECASE), -1),
    (re.compile(rf"\b(?:increase|raise|hike|lift)\w*", re.IGNORECASE), 1),
)

# ---------------------------------------------------------------------------
# Decision-level wording, balance-sheet / asset purchases, forward guidance.
# ---------------------------------------------------------------------------
# The Governing Council "…decided (today) to … / …decided that …" sentences are
# the decision wording, kept verbatim as facts.
_DECISION_STATEMENT = re.compile(r"\bdecided\s+(?:today\s+)?(?:to|that)\b", re.IGNORECASE)

# Programme sections carry the asset-purchase / balance-sheet decisions. The
# programme identity is preserved as the Fact's ``identity_qualifier``.
_PROGRAMME_SECTIONS: tuple[tuple[str, str], ...] = (
    ("asset purchase programme", "app"),
    ("pandemic emergency purchase programme", "pepp"),
    ("targeted longer-term refinancing operations", "tltro"),
)

# Explicit forward-guidance anchors: prospective *policy* statements about the
# governing council's own instruments/rates, part of the decision. Anchors are
# intentionally narrow — they never match economic analysis (Phase 4.2).
_GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bstands?\s+ready\s+to\s+adjust\b", re.IGNORECASE),
    re.compile(r"\bwill\s+not\s+hesitate\s+to\s+adjust\b", re.IGNORECASE),
    re.compile(r"\bfor\s+as\s+long\s+as\s+necessary\b", re.IGNORECASE),
    re.compile(r"\bdoes\s+not\s+pre-?commit\b", re.IGNORECASE),
    re.compile(r"\bexpects?\s+(?:the\s+)?(?:key\s+)?(?:ecb\s+)?interest\s+rates?\s+to\s+remain\b", re.IGNORECASE),
    re.compile(r"\bwill\s+keep\s+(?:the\s+)?(?:key\s+)?(?:ecb\s+)?interest\s+rates?\b", re.IGNORECASE),
    re.compile(r"\b(?:maintain|maintaining)\s+(?:an?\s+|the\s+)?(?:accommodative|restrictive)\b", re.IGNORECASE),
)

# "… during the first/second half of <year>", "… until the end of <year>",
# "… in <year>" — the relevant period of a reinvestment decision.
_PERIOD_DURING_HALF = re.compile(
    r"\b(?:durin[g]\s+the\s+|in\s+the\s+)?(first|second)\s+half\s+of\s+(?P<year>[0-9]{4})\b",
    re.IGNORECASE,
)
_PERIOD_IN_YEAR = re.compile(r"\b(?:in|during|until\s+the\s+end\s+of)\s+(?P<year>[0-9]{4})\b", re.IGNORECASE)


def _split_sentences(text: str) -> list[str]:
    """Split normalized section text into non-empty sentences, each verbatim
    (trailing period preserved). Both ``". "`` and ``".\\n"`` are boundaries."""
    return [part.strip() for part in re.split(r"(?<=\.)\s+", text or "") if part.strip()]


def _phrase_re(phrase: str) -> str:
    """Match either "interest rate on the <phrase>" or "<phrase> rate"."""
    escaped = re.escape(phrase)
    return rf"(?:interest\s+rate\s+on\s+the\s+{escaped}|{escaped}\s+rate)"


def _level_pattern(phrase: str) -> re.Pattern:
    """Per-instrument level statement, e.g.
    "lower the deposit facility rate to 1.75 per cent",
    "the interest rate on the main refinancing operations was reduced to 1.75 per cent",
    "the deposit facility rate remains at 1.75 per cent"."""
    return re.compile(
        rf"(?:"
        rf"{_LEVEL_VERBS}\w*\s+(?:the\s+)?{_phrase_re(phrase)}"  # active: verb … instrument rate
        rf"|"
        rf"(?:the\s+)?{_phrase_re(phrase)}\s+(?:was|were|is|are|will\s+be|has\s+been|have\s+been)\s+{_LEVEL_VERBS}\w*"  # passive
        rf"|"
        rf"(?:the\s+)?{_phrase_re(phrase)}\s+(?:remain(?:s|ed)?|stays?|stayed)"  # hold: instrument rate remains at …
        rf")"
        rf"[^.]*?\b(?:to|at)\s+(?P<token>{_VALUE_TOKEN})",
        re.IGNORECASE,
    )


def _change_pattern(phrase: str) -> re.Pattern:
    """Per-instrument explicit change, e.g.
    "lower the deposit facility rate by 25 basis points",
    "the marginal lending facility rate was cut by 25 basis points"."""
    return re.compile(
        rf"(?:"
        rf"{_DIRECTIONAL_VERBS}\w*\s+(?:the\s+)?{_phrase_re(phrase)}"  # active
        rf"|"
        rf"(?:the\s+)?{_phrase_re(phrase)}\s+(?:was|were|is|are|will\s+be|has\s+been|have\s+been)\s+{_DIRECTIONAL_VERBS}\w*"  # passive
        rf")"
        rf"[^.]*?\bby\s+(?P<amount>{_RATE_ITEM})\s+basis\s+points?",
        re.IGNORECASE,
    )


def _sentence_bounds(text: str, position: int) -> tuple[int, int]:
    """(start, end) of the sentence containing ``position``, as exact offsets.

    Sentences end at ``.`` followed by any whitespace (newlines are paragraph
    breaks in the normalized text, so both ". " and ".\n" are boundaries)."""
    end_match = re.search(r"\.\s", text[position:])
    end = position + end_match.end() if end_match else len(text)
    starts = list(re.finditer(r"\.\s", text[:position]))
    start = starts[-1].end() if starts else 0
    return start, end


class EcbDecisionExtractor(DecisionExtractor):
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

        decision_date = self._decision_date(document)
        if decision_date is None:
            result.warnings.append("no_decision_date")

        rates_section_index = self._rates_section_index(document)
        if rates_section_index is None:
            result.warnings.append("no_rates_section")

        effective = self._effective_date(document, rates_section_index)

        if decision_date is not None:
            iso_date, raw, index = decision_date
            dt = parse_datetime(iso_date)
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_DECISION,
                    predicate=PREDICATE_DATE,
                    value=date_value(iso_date, source_text=raw),
                    effective_date=dt,
                    source_location=FactLocation(LocationKind.SECTION, section=index),
                    source_text=raw,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                )
            )

        levels, warnings = self._rate_levels(document)
        result.warnings.extend(warnings)
        for subject, section_index, token, value, source in levels:
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=subject,
                    predicate=PREDICATE_VALUE,
                    value=percentage(value, source_text=token),
                    effective_date=effective,
                    source_location=FactLocation(LocationKind.SECTION, section=section_index),
                    source_text=source,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                )
            )

        for subject, section_index, source, amount, delta in self._rate_changes(document):
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=subject,
                    predicate=PREDICATE_CHANGE,
                    value=basis_points(delta, source_text=amount),
                    effective_date=effective,
                    source_location=FactLocation(LocationKind.SECTION, section=section_index),
                    source_text=source,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                )
            )

        for offset, section_index, sentence in self._decision_wordings(document):
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_DECISION,
                    predicate=PREDICATE_STATEMENT,
                    value=FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
                    source_location=FactLocation(LocationKind.SECTION, section=section_index),
                    source_text=sentence,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                    identity_qualifier=f"statement:{offset}",
                )
            )

        for offset, section_index, programme, sentence, period in self._asset_purchase_decisions(document):
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_ASSET_PURCHASE,
                    predicate=PREDICATE_DECISION,
                    value=FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
                    period=period,
                    source_location=FactLocation(LocationKind.SECTION, section=section_index),
                    source_text=sentence,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                    identity_qualifier=f"{programme}:{offset}",
                )
            )

        for offset, section_index, sentence in self._forward_guidance(document):
            result.add(
                Fact(
                    publication_id=result.publication_id,
                    document_id=document.document_id,
                    subject=SUBJECT_POLICY_GUIDANCE,
                    predicate=PREDICATE_STATEMENT,
                    value=FactValue(ValueKind.TEXT, value=sentence, source_text=sentence),
                    source_location=FactLocation(LocationKind.SECTION, section=section_index),
                    source_text=sentence,
                    extraction_method=METHOD_REGEX,
                    extraction_version=EXTRACTION_VERSION,
                    confidence=Confidence.HIGH,
                    identity_qualifier=f"guidance:{offset}",
                )
            )
        return result

    # ------------------------------------------------------------------
    # decision date
    # ------------------------------------------------------------------
    @staticmethod
    def _decision_date(document) -> tuple[str, str, int] | None:
        """Structured decision-date extraction, preferring the ECB layout.

        1. the leading date paragraph — heading-less section(s) at the top,
           before the first heading (normally the ``ecb-publicationDate``
           paragraph);
        2. structural fallback — the "Monetary policy decisions" section itself.

        An arbitrary date that appears elsewhere in the document (e.g. in a
        rates or press-conference section) is deliberately never used. Returns
        (ISO date, raw date text, section index) or None.
        """
        for index, section in enumerate(document.sections):
            if section.heading:
                break  # previous heading-less sections are the pre-title paragraphs
            match = _DATE_TOKEN.search(section.text or "")
            if match:
                dt = parse_datetime(match.group(0))
                if dt is not None:
                    return dt.date().isoformat(), match.group(0), index
        for index, section in enumerate(document.sections):
            if normalize_title(section.heading or "") != "monetary policy decisions":
                continue
            match = _DATE_TOKEN.search(section.text or "")
            if match:
                dt = parse_datetime(match.group(0))
                if dt is not None:
                    return dt.date().isoformat(), match.group(0), index
            return None
        return None

    # ------------------------------------------------------------------
    # rates section / effective date
    # ------------------------------------------------------------------
    @staticmethod
    def _rates_section_index(document) -> int | None:
        for index, section in enumerate(document.sections):
            if normalize_title(section.heading or "") == "key ecb interest rates":
                return index
        return None

    @staticmethod
    def _effective_date(document, rates_section_index: int | None) -> datetime | None:
        """Effective date from the section where the rates are stated (rates
        section, else the decision section). Never an arbitrary date."""
        for index in (rates_section_index,):
            if index is None:
                continue
            match = _EFFECTIVE_DATE.search(document.sections[index].text or "")
            if match:
                return parse_datetime(match.group("date"))
        return None

    # ------------------------------------------------------------------
    # rate levels
    # ------------------------------------------------------------------
    @classmethod
    def _rate_levels(cls, document) -> tuple[list, list]:
        """Rate level facts with verbatim provenance.

        Pass 1: the combined "… to A%, B% and C% respectively" enumeration. The
        instrument order is read from the enclosing sentence (the "respectively"
        construction makes the ordering the *source's* structure — mapping item
        i to the i-th named instrument is explicit semantic identification, not
        an assumption). If all three instruments cannot be identified, a warning
        is emitted and nothing is extracted from that sentence.

        Pass 2: per-instrument statements fill any rate the enumeration did not
        cover (or a document without an enumeration).
        """
        found: dict[str, tuple[int, str, float, str]] = {}
        warnings: list[str] = []

        for index, section in enumerate(document.sections):
            text = section.text or ""
            enum = _RATES_ENUMERATED.search(text)
            if not enum:
                continue
            start, end = _sentence_bounds(text, enum.start())
            sentence = text[start:end]
            order = cls._instrument_order(sentence)
            if len(order) != 3:
                warnings.append("rate_items_unresolved")
                continue
            tokens = (enum.group("a"), enum.group("b"), enum.group("c"))
            for subject, token in zip(order, tokens):
                value = float(token.rstrip("%"))
                found[subject] = (index, token, value, sentence.strip())
            break  # the first resolvable enumeration is authoritative

        for phrase, subject in _INSTRUMENT_SUBJECTS:
            if subject in found:
                continue
            for index, section in enumerate(document.sections):
                match = _level_pattern(phrase).search(section.text or "")
                if not match:
                    continue
                token = match.group("token")
                value = float(re.match(r"[0-9.]+", token).group(0))
                found[subject] = (index, token, value, match.group(0))
                break

        levels = [(subject, idx, token, value, source) for subject, (idx, token, value, source) in found.items()]
        return levels, warnings

    @staticmethod
    def _instrument_order(sentence: str) -> list[str]:
        """Instruments as named in the enumeration sentence, in naming order.

        Only the sentence text before "respectively" is considered. Returns an
        empty list when fewer than three instruments are deterministically
        identified — callers must then warn instead of assuming an order.
        """
        lower = sentence.lower()
        cutoff = lower.rfind("respectively")
        window = lower if cutoff == -1 else lower[:cutoff]
        positions = [(window.find(phrase), subject) for phrase, subject in _INSTRUMENT_SUBJECTS if window.find(phrase) != -1]
        positions.sort(key=lambda item: item[0])
        order = [subject for _, subject in positions]
        return order if len(order) == 3 else []

    # ------------------------------------------------------------------
    # rate changes
    # ------------------------------------------------------------------
    @classmethod
    def _rate_changes(cls, document) -> list:
        """Explicit, directional rate changes ("by N basis points").

        Specific per-instrument sentences are read first (deterministic
        attribution by instrument name). Sentences that say "*the three key ECB
        interest rates* … by N basis points" are attributed to the canonical
        three-rate set — a definitional mapping, not an invention. Ambiguous
        changes (no instrument, no key-rates context) are skipped silently.
        """
        changes: list = []
        seen: set = set()

        for index, section in enumerate(document.sections):
            text = section.text or ""
            for phrase, subject in _INSTRUMENT_SUBJECTS:
                match = _change_pattern(phrase).search(text)
                if not match:
                    continue
                span = match.group(0)
                delta = round(cls._sign_of(span) * float(match.group("amount")), 2)
                if (subject, delta) in seen:
                    continue
                seen.add((subject, delta))
                changes.append((subject, index, span, match.group(0), delta))

        for index, section in enumerate(document.sections):
            text = section.text or ""
            for match in _AMOUNT_BY.finditer(text):
                start, end = _sentence_bounds(text, match.start())
                sentence = text[start:end]
                subjects = cls._subjects_in_sentence(sentence)
                if not subjects:
                    continue
                delta = round(cls._sign_of(sentence) * float(match.group("amount")), 2)
                for subject in subjects:
                    if (subject, delta) in seen:
                        continue
                    seen.add((subject, delta))
                    changes.append((subject, index, sentence.strip(), match.group(0), delta))

        return changes

    @staticmethod
    def _subjects_in_sentence(sentence: str) -> list[str]:
        """Instruments explicitly named in a sentence; falling back to the
        canonical three-rate set only for a *key-rates* context sentence."""
        lower = sentence.lower()
        subjects = [subject for phrase, subject in _INSTRUMENT_SUBJECTS if phrase in lower]
        if subjects:
            return subjects
        if _BROAD_CONTEXT.search(sentence):
            return list(_CANONICAL_ORDER)
        return []

    @staticmethod
    def _sign_of(text: str) -> int:
        """Direction of the first directional verb in ``text``: easing verbs
        negative, tightening verbs positive."""
        best_sign, best_pos = 1, -1
        for pattern, sign in _DIRECTION_MARKERS:
            match = pattern.search(text)
            if match and match.start() > best_pos:
                best_sign, best_pos = sign, match.start()
        return best_sign

    # ------------------------------------------------------------------
    # decision wording
    # ------------------------------------------------------------------
    @staticmethod
    def _decision_wordings(document) -> list[tuple[int, int, str]]:
        """The decision's own wording, verbatim: every "Governing Council …
        decided (today) to …" sentence in the decision section. Returns
        (ordinal, section index, sentence)."""
        found: list[tuple[int, int, str]] = []
        for index, section in enumerate(document.sections):
            if normalize_title(section.heading or "") != "monetary policy decisions":
                continue
            for sentence in _split_sentences(section.text or ""):
                if _DECISION_STATEMENT.search(sentence):
                    found.append((len(found), index, sentence))
            break  # the decision section is authoritative for the wording
        return found

    # ------------------------------------------------------------------
    # asset purchases / balance-sheet decisions
    # ------------------------------------------------------------------
    @staticmethod
    def _asset_purchase_decisions(document) -> list[tuple[int, int, str, str, object]]:
        """Explicit programme decisions (reinvestment, cessation, continuation,
        maturity handling) from the APP / PEPP / TLTRO sections. Every non-empty
        sentence of those sections is a source-level decision statement — nothing
        is inferred from absence. Returns (ordinal, section index, programme,
        sentence, FactPeriod | None)."""
        found: list[tuple[int, int, str, str, object]] = []
        counts: dict[str, int] = {}
        for index, section in enumerate(document.sections):
            programme = None
            for key, code in _PROGRAMME_SECTIONS:
                if key in normalize_title(section.heading or ""):
                    programme = code
                    break
            if programme is None:
                continue
            for sentence in _split_sentences(section.text or ""):
                period = EcbDecisionExtractor._period_from_text(sentence)
                ordinal = counts.get(programme, 0)
                counts[programme] = ordinal + 1
                found.append((ordinal, index, programme, sentence, period))
        return found

    @staticmethod
    def _period_from_text(text: str) -> FactPeriod | None:
        """Relevant period of a programme decision, verbatim label preserved
        (e.g. "during the first half of 2027" → semester 2027-H1)."""
        half = _PERIOD_DURING_HALF.search(text)
        if half:
            half_id = "1" if half.group(1).lower() == "first" else "2"
            return FactPeriod(PeriodKind.SEMESTER, f"{half.group('year')}-H{half_id}", label=half.group(0))
        year = _PERIOD_IN_YEAR.search(text)
        if year:
            return FactPeriod(PeriodKind.YEAR, year.group("year"), label=year.group(0))
        return None

    # ------------------------------------------------------------------
    # forward guidance
    # ------------------------------------------------------------------
    @classmethod
    def _forward_guidance(cls, document) -> list[tuple[int, int, str]]:
        """Explicit prospective policy statements that are part of the decision,
        verbatim. Only decision-level sections are considered — the separate
        "Monetary policy statement" is Phase 4.2 territory. Returns (ordinal,
        section index, sentence)."""
        found: list[tuple[int, int, str]] = []
        for index, section in enumerate(document.sections):
            if "monetary policy statement" in normalize_title(section.heading or ""):
                continue
            for sentence in _split_sentences(section.text or ""):
                for anchor in _GUIDANCE_ANCHORS:
                    if anchor.search(sentence):
                        found.append((len(found), index, sentence))
                        break
        return found