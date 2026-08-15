"""Structural Speech extraction pipeline — shared by the bank-specific
extractors (Phase 4.x — Multi-Bank Fact Extraction / Speech family).

This module implements the **whole extraction pipeline** with **no
bank-specific semantics baked in**:

- section routing (known non-economic / known economic / unknown headings,
  analytical boxes, ``clean_heading`` normalization);
- content-first sentence categorization with fixed precedence
  (guidance > policy > risk > financial > inflation > labour > growth);
- categorical risk-orientation vs verbatim assessment emission;
- qualitative / quantitative value emission through the shared ``Reporter``;
- the run-state warnings (``no_risk_assessment`` / ``no_forward_guidance`` /
  ``quoted_content_skipped``) and the Speech ``extract`` contract.

Everything that is genuinely bank-specific — the section-heading vocabulary,
the guidance anchors, the policy term / stance vocabulary, and any bank-own
subject resolution — lives on the concrete subclass as class-level hooks
(`IGNORE_HEADINGS/_ECONOMIC_HEADINGS`, `GUIDANCE_ANCHORS`, `POLICY_TERM …`,
the `*_subject()` methods, and the like). Nothing here knows about any
particular central bank, and there is no ``if bank == "…":`` dispatch.

The genuinely generic English financial anchors (inflation / financial /
labour / growth / risk vocabulary, the GDP near-miss guard) are shared as
class-level defaults — they are structural, not bank-identity. ECB is kept as
the standalone reference implementation; the multi-bank minors share this base.
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

# ---------------------------------------------------------------------------
# Section routing — CONSERVATIVE (precision over recall). A heading is mined in
# full only when it is a known economic section; a known non-economic heading
# (biography, thanks, closing remarks, Q&A, legal/back matter) is ignored; an
# **unknown heading** is mined at paragraph level but never yields an automatic
# fact — only explicit assertions pass. "Absence of proof → absence of
# extraction".
# ---------------------------------------------------------------------------
CAT_IGNORE = "ignore"
CAT_UNKNOWN = "unknown"
CAT_ECONOMIC = "economic"

CAT_NONE = "none"
CAT_GUIDANCE = "guidance"
CAT_POLICY = "policy"
CAT_RISK = "risk"
CAT_FINANCIAL = "financial"
CAT_INFLATION = "inflation"
CAT_LABOUR = "labour"
CAT_GROWTH = "growth"

# Shared, bank-agnostic English financial vocabulary (structural). A subclass
# may override any of these, but they are deliberately generic — no bank
# identity (institution names, trademarked indicators, currency-named measures)
# appears here.
RISK_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\brisks?\s+(?:to|for|around|surrounding|from|of|are|were|remain|remained|have|has)\b", re.IGNORECASE),
    re.compile(r"\b(?:downside|upside|two-sided|symmetric|broadly\s+balanced)\s+risks?\b", re.IGNORECASE),
    re.compile(r"\buncertain(?:ty|ties)?\b", re.IGNORECASE),
    re.compile(r"\btilted\b", re.IGNORECASE),
)

FINANCIAL_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bfinancial conditions\b", re.IGNORECASE),
    re.compile(r"\bfinancing conditions?\b", re.IGNORECASE),
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

INFLATION_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\binflation\b", re.IGNORECASE),
    re.compile(r"\bdisinflation\b", re.IGNORECASE),
    re.compile(r"\bcore\s+inflation\b", re.IGNORECASE),
    re.compile(r"\bdeflation\b", re.IGNORECASE),
    re.compile(r"\bprice pressures\b", re.IGNORECASE),
    re.compile(r"\benergy prices\b", re.IGNORECASE),
    re.compile(r"\bfood prices\b", re.IGNORECASE),
    re.compile(r"\bconsumer prices\b", re.IGNORECASE),
)

LABOUR_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\blabou?r market\b", re.IGNORECASE),
    re.compile(r"\bunemployment\b", re.IGNORECASE),
    re.compile(r"\bemployment\b(?!\s+policy\b)", re.IGNORECASE),
    re.compile(r"\bwage(?:s)?\b(?!\s+policy\b)", re.IGNORECASE),
)

_GDP_NEAR_MISS = re.compile(
    r"(?:\bgdp\b\s+(?:deflator|per\s+capita)\b|per\s+capita\s+\bgdp\b)",
    re.IGNORECASE,
)
_GDP_ANCHOR = re.compile(
    r"(?<!per\scapita\s)\bgdp\b(?!\s+(?:deflator|per\s+capita)\b)",
    re.IGNORECASE,
)

GROWTH_ANCHORS: tuple[re.Pattern, ...] = (
    re.compile(r"\bgrowth\b", re.IGNORECASE),
    _GDP_ANCHOR,
    re.compile(r"\b(?:economic\s+)?activity\b", re.IGNORECASE),
    re.compile(r"\beconom(?:y|ic)\b", re.IGNORECASE),
    re.compile(r"\boutput\b", re.IGNORECASE),
    re.compile(r"\b(?:domestic|aggregate|global|external|private|overall|total)\s+demand\b", re.IGNORECASE),
    re.compile(r"\bconsumption\b", re.IGNORECASE),
    re.compile(r"\binvestment\b", re.IGNORECASE),
    re.compile(r"\b(?:industrial|manufacturing|energy|oil|steel|automotive)\s+production\b", re.IGNORECASE),
)


class _RunState:
    """Mutable run state threaded through the sentence miners."""

    __slots__ = ("risk_found", "guidance_found", "quoted_skipped")

    def __init__(self) -> None:
        self.risk_found = False
        self.guidance_found = False
        self.quoted_skipped = False


class SpeechExtractorBase(SpeechExtractor):
    """Structural multi-bank speech extractor. Subclass per bank and override
    the vocabulary hooks; ``extract`` and the mined pipeline are shared."""

    # --- vocabulary hooks (bank-specific; must be overridden) --------------
    IGNORE_HEADINGS: frozenset[str] = frozenset()
    ECONOMIC_HEADINGS: frozenset[str] = frozenset()
    GUIDANCE_ANCHORS: tuple[re.Pattern, ...] = ()
    POLICY_TERM: re.Pattern | None = None
    POLICY_STANCE: re.Pattern | None = None
    POLICY_STANCE_PHRASE: re.Pattern | None = None
    # --- generic English financial vocabulary (shared defaults) ------------
    RISK_ANCHORS = RISK_ANCHORS
    FINANCIAL_ANCHORS = FINANCIAL_ANCHORS
    INFLATION_ANCHORS = INFLATION_ANCHORS
    LABOUR_ANCHORS = LABOUR_ANCHORS
    GROWTH_ANCHORS = GROWTH_ANCHORS
    GDP_NEAR_MISS = _GDP_NEAR_MISS

    # ------------------------------------------------------------------
    # section routing
    # ------------------------------------------------------------------
    def _section_category(self, heading: str) -> str:
        """Route a section by its normalized heading: ``CAT_IGNORE`` (a known
        non-economic controlled heading or a heading-less section / analytical
        box), ``CAT_ECONOMIC`` (a known economic heading, mined in full), or
        ``CAT_UNKNOWN`` (strictly mined, explicit assertions only). Exact
        membership only; substring coincidence never determines identity.
        Heading normalization is the shared structural `clean_heading`."""
        t = clean_heading(heading or "")
        if not t or is_box(t):
            return CAT_IGNORE
        if t in self.IGNORE_HEADINGS:
            return CAT_IGNORE
        if t in self.ECONOMIC_HEADINGS:
            return CAT_ECONOMIC
        return CAT_UNKNOWN

    def extract(self, publication, document: NormalizedDocument) -> ExtractionResult:
        result = ExtractionResult(
            publication_id=publication.id or publication.dedup_key or "",
            document_id=document.document_id,
        )
        if not document.sections:
            result.warnings.append("no_sections")
            return result

        speaker = speaker_from_document(document)
        reporter = Reporter(extraction_version=self.extraction_version)
        state = _RunState()

        for index, section in enumerate(document.sections):
            category = self._section_category(section.heading or "")
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
    # section walking
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
            subject = self._risk_subject(sentence)
            reporter.emit_risk(result, document, index, sentence, subject, speaker, strict=strict)
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
        if self._matches(self.GUIDANCE_ANCHORS, sentence):
            return CAT_GUIDANCE
        if self._is_policy_sentence(sentence):
            return CAT_POLICY
        if self._matches(self.RISK_ANCHORS, sentence):
            return CAT_RISK
        if self._matches(self.FINANCIAL_ANCHORS, sentence):
            return CAT_FINANCIAL
        if self._matches(self.INFLATION_ANCHORS, sentence):
            return CAT_INFLATION
        if self._matches(self.LABOUR_ANCHORS, sentence):
            return CAT_LABOUR
        if self._matches(self.GROWTH_ANCHORS, sentence):
            return CAT_GROWTH
        return CAT_NONE

    @staticmethod
    def _matches(anchors: tuple[re.Pattern, ...], sentence: str) -> bool:
        return any(anchor.search(sentence) for anchor in anchors)

    def _is_policy_sentence(self, sentence: str) -> bool:
        if not (self.POLICY_STANCE and self.POLICY_TERM):
            return bool(self.POLICY_STANCE_PHRASE and self.POLICY_STANCE_PHRASE.search(sentence))
        if bool(self.POLICY_STANCE.search(sentence) and self.POLICY_TERM.search(sentence)):
            return True
        return bool(self.POLICY_STANCE_PHRASE and self.POLICY_STANCE_PHRASE.search(sentence))

    # ------------------------------------------------------------------
    # subject resolution (bank-specific where the vocabulary differs)
    # ------------------------------------------------------------------
    def _risk_subject(self, sentence: str) -> str:
        lower = sentence.lower()
        if "inflation" in lower:
            return SUBJECT_INFLATION_RISK
        if "growth" in lower or "activity" in lower or "gdp" in lower:
            return SUBJECT_GROWTH_RISK
        return SUBJECT_RISK

    def _inflation_subject(self, sentence: str) -> str:
        lower = sentence.lower()
        if "inflation expectations" in lower:
            return SUBJECT_INFLATION_EXPECTATIONS
        if "core inflation" in lower:
            return SUBJECT_CORE_INFLATION
        return SUBJECT_INFLATION

    def _labour_subject(self, sentence: str) -> str:
        lower = sentence.lower()
        if "unemployment" in lower:
            return SUBJECT_UNEMPLOYMENT
        if "wage" in lower:
            return SUBJECT_WAGES
        return SUBJECT_LABOUR_MARKET

    def _add_inflation_facts(
        self, result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, reporter: Reporter, speaker: str | None, *, strict: bool
    ) -> None:
        subject = self._inflation_subject(sentence)
        if reporter.emit_value_facts(result, document, index, sentence, subject, speaker):
            return
        if not strict and is_economic_assertion(sentence):
            reporter.emit_text(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, speaker)

    def _add_labour_facts(
        self, result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, reporter: Reporter, speaker: str | None, *, strict: bool
    ) -> None:
        subject = self._labour_subject(sentence)
        if reporter.emit_value_facts(result, document, index, sentence, subject, speaker):
            return
        if not strict and is_economic_assertion(sentence):
            reporter.emit_text(result, document, index, sentence, subject, PREDICATE_ASSESSMENT, speaker)

    def _add_growth_facts(
        self, result: ExtractionResult, document: NormalizedDocument, index: int, sentence: str, reporter: Reporter, speaker: str | None, *, strict: bool
    ) -> None:
        # A GDP deflator / per-capita mention inside an otherwise-growth
        # sentence must not leak into a GDP value fact (precision first).
        if self.GDP_NEAR_MISS.search(sentence):
            return
        if reporter.emit_value_facts(result, document, index, sentence, SUBJECT_GDP, speaker):
            return
        if not strict and is_economic_assertion(sentence):
            reporter.emit_text(result, document, index, sentence, SUBJECT_GROWTH, PREDICATE_ASSESSMENT, speaker)


__all__ = [
    "CAT_IGNORE", "CAT_UNKNOWN", "CAT_ECONOMIC",
    "CAT_NONE", "CAT_GUIDANCE", "CAT_POLICY", "CAT_RISK", "CAT_FINANCIAL",
    "CAT_INFLATION", "CAT_LABOUR", "CAT_GROWTH",
    "SpeechExtractorBase",
]
