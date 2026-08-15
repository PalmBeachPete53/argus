"""BoC — Bank of Canada statement extractor (Phase 6).

Encapsulates the Bank of Canada's vocabulary on top of the shared Phase 6
engine. BoC language uses "per cent"; the reference date is the "July 24, 2026"
style release date. Section headings mirror the Monetary Policy Report
structure (inflation, the labour market, economic activity, risks).
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


class BocStatementExtractor(BankStatementExtractor):
    bank = "boc"
    extraction_version = EXTRACTION_VERSION

    intro_headings = frozenset({
        "bank of canada",
        "monetary policy",
        "monetary policy report",
        "press release",
        "decision",
    })
    risk_headings = frozenset({"risks", "risk assessment", "risks around the outlook"})
    guidance_headings = frozenset({"forward guidance", "guidance", "policy stance", "monetary policy stance"})
    inflation_headings = frozenset({"inflation", "cpi inflation", "inflation outlook", "price dynamics"})
    growth_headings = frozenset({
        "economic activity",
        "economic outlook",
        "the economic outlook",
        "global economic growth",
        "the economy",
    })
    labour_headings = frozenset({"the labour market", "labour market", "employment"})
    financial_headings = frozenset({"financial conditions", "financial markets"})

    guidance_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\bthe\s+bank\s+(?:will|would)\s+continue\s+to\b", re.IGNORECASE),
        re.compile(r"\bthe\s+bank\s+stands\s+ready\s+to\b", re.IGNORECASE),
        re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
        re.compile(r"\bwill\s+depend\s+on\b", re.IGNORECASE),
        re.compile(r"\bdata-dependent\b", re.IGNORECASE),
        re.compile(r"\bassess\s+the\s+appropriate\s+pace\b", re.IGNORECASE),
        re.compile(r"\bdoes\s+not\s+pre-?commit\b", re.IGNORECASE),
    )

    risk_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\brisks?\s+(?:to|around|surrounding|from|of|are|were|remain|have|has|posed)\b", re.IGNORECASE),
        re.compile(r"\b(?:downside|upside|two-sided|symmetric|broadly\s+balanced)\s+risks?\b", re.IGNORECASE),
        re.compile(r"\btilted\b", re.IGNORECASE),
        re.compile(r"\buncertain(?:ty|ties)?\b", re.IGNORECASE),
    )

    rationale_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\bconsistent with\b", re.IGNORECASE),
        re.compile(r"\bin order to\b", re.IGNORECASE),
        re.compile(r"\bto ensure that\b", re.IGNORECASE),
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
    "BocStatementExtractor",
]