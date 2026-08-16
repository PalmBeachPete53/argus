"""RBNZ — Reserve Bank of New Zealand statement extractor (Phase 4.2).

Encapsulates the RBI's (Reserve Bank of New Zealand) vocabulary on top of the
shared Phase 4.2 engine. RBNZ language uses "percent"; the release date is the
"19 November 2026" style release date. Section headings mirror the Monetary
Policy Statement structure (the current situation, the outlook, inflation).
"""

from __future__ import annotations

import re

from ._shared import (
    PREDICATE_ASSESSMENT,
    PREDICATE_RATIONALE,
    PREDICATE_STATEMENT,
    SUBJECT_GROWTH,
    SUBJECT_INFLATION,
    SUBJECT_MONETARY_POLICY,
    SUBJECT_POLICY_GUIDANCE,
    BankStatementExtractor,
)

EXTRACTION_VERSION = "6.1.0"

SUBJECT_INFLATION = SUBJECT_INFLATION
SUBJECT_GROWTH = SUBJECT_GROWTH
SUBJECT_POLICY_GUIDANCE = SUBJECT_POLICY_GUIDANCE


class RbnzStatementExtractor(BankStatementExtractor):
    bank = "rbnz"
    extraction_version = EXTRACTION_VERSION

    intro_headings = frozenset({
        "reserve bank of new zealand",
        "official cash rate",
        "monetary policy statement",
        "press release",
    })
    risk_headings = frozenset({"risks", "risk assessment", "risks to the outlook", "key judgements"})
    guidance_headings = frozenset({"forward guidance", "guidance", "policy stance", "monetary policy stance"})
    inflation_headings = frozenset({"inflation", "consumer price inflation", "inflation outlook", "price stability"})
    growth_headings = frozenset({
        "the current situation",
        "current situation",
        "the outlook",
        "economic outlook",
        "economic activity",
        "the economy",
    })
    labour_headings = frozenset({"the labour market", "labour market", "employment"})
    financial_headings = frozenset({"financial conditions", "financial markets", "monetary conditions"})

    guidance_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\bthe\s+committee\s+(?:agreed|decided)s?\s+that\b", re.IGNORECASE),
        re.compile(r"\bwill\s+maintain\s+an?\s+appropriate\s+(?:level|stance)\b", re.IGNORECASE),
        re.compile(r"\bstands?\s+ready\s+to\b", re.IGNORECASE),
        re.compile(r"\bwill\s+not\s+hesitate\s+to\b", re.IGNORECASE),
        re.compile(r"\bwill\s+continue\s+to\s+(?:assess|monitor|reassess)\b", re.IGNORECASE),
        re.compile(r"\bwill\s+depend\s+on\b", re.IGNORECASE),
        re.compile(r"\bdata-dependent\b", re.IGNORECASE),
    )

    risk_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\brisks?\s+(?:to|around|surrounding|from|of|are|were|remain|have|has|posed|associated)\b", re.IGNORECASE),
        re.compile(r"\b(?:downside|upside|two-sided|symmetric|broadly\s+balanced)\s+risks?\b", re.IGNORECASE),
        re.compile(r"\btilted\b", re.IGNORECASE),
        re.compile(r"\buncertain(?:ty|ties)?\b", re.IGNORECASE),
    )

    rationale_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\bto\s+ensure\s+that\b", re.IGNORECASE),
        re.compile(r"\bconsistent with\b", re.IGNORECASE),
        re.compile(r"\bin order to\b", re.IGNORECASE),
        re.compile(r"\bbased on\b", re.IGNORECASE),
    )


__all__ = [
    "EXTRACTION_VERSION",
    "SUBJECT_INFLATION",
    "SUBJECT_GROWTH",
    "SUBJECT_POLICY_GUIDANCE",
    "PREDICATE_ASSESSMENT",
    "PREDICATE_RATIONALE",
    "PREDICATE_STATEMENT",
    "SUBJECT_MONETARY_POLICY",
    "RbnzStatementExtractor",
]