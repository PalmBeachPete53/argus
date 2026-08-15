"""SNB — Swiss National Bank statement extractor (Phase 6).

Encapsulates the SNB's quarterly "Monetary policy assessment" vocabulary on top
of the shared Phase 6 engine. The SNB releases a "Monetary policy assessment as
of 19 June 2026" whose narrative sections (economic conditions, inflation
outlook, monetary conditions) carry the statements.
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
    SUBJECT_FINANCIAL_CONDITIONS,
    SUBJECT_POLICY_GUIDANCE,
    BankStatementExtractor,
)

EXTRACTION_VERSION = "6.1.0"

SUBJECT_INFLATION = SUBJECT_INFLATION
SUBJECT_GROWTH = SUBJECT_GROWTH
SUBJECT_POLICY_GUIDANCE = SUBJECT_POLICY_GUIDANCE
SUBJECT_FINANCIAL_CONDITIONS = SUBJECT_FINANCIAL_CONDITIONS


class SnbStatementExtractor(BankStatementExtractor):
    bank = "snb"
    extraction_version = EXTRACTION_VERSION

    intro_headings = frozenset({
        "monetary policy assessment",
        "swiss national bank",
        "on the assessment of the economic and inflationary situation",
        "press release",
    })
    risk_headings = frozenset({"risks", "risk assessment", "risks and uncertainties"})
    guidance_headings = frozenset({"monetary conditions", "monetary policy conditions", "policy stance"})
    inflation_headings = frozenset({"inflation outlook", "inflation", "price developments"})
    growth_headings = frozenset({"economic conditions", "economic outlook", "the economy", "economic activity"})
    labour_headings = frozenset({"employment", "the labour market"})
    financial_headings = frozenset({"monetary and financial conditions", "financial conditions"})

    guidance_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\bthe\s+snb\s+(?:is\s+)?continuing\s+to\b", re.IGNORECASE),
        re.compile(r"\bthe\s+snb\s+will\s+continue\s+to\b", re.IGNORECASE),
        re.compile(r"\bthe\s+snb\s+stands\s+ready\s+to\b", re.IGNORECASE),
        re.compile(r"\bto\s+ensure\s+appropriate\s+monetary\s+conditions\b", re.IGNORECASE),
        re.compile(r"\bwill\s+continue\s+to\s+monitor\b", re.IGNORECASE),
        re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
        re.compile(r"\bdata-dependent\b", re.IGNORECASE),
    )

    risk_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\brisks?\s+(?:to|from|of|are|were|remain|remain|have|has|posed|associated)\b", re.IGNORECASE),
        re.compile(r"\b(?:downside|upside|two-sided|symmetric|broadly\s+balanced)\s+risks?\b", re.IGNORECASE),
        re.compile(r"\btilted\b", re.IGNORECASE),
        re.compile(r"\buncertain(?:ty|ties)?\b", re.IGNORECASE),
    )

    rationale_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\bto\s+keep\s+inflation\b", re.IGNORECASE),
        re.compile(r"\bconsistent with\b", re.IGNORECASE),
        re.compile(r"\bin order to\b", re.IGNORECASE),
        re.compile(r"\bbased on\b", re.IGNORECASE),
    )


__all__ = [
    "EXTRACTION_VERSION",
    "SUBJECT_INFLATION",
    "SUBJECT_GROWTH",
    "SUBJECT_POLICY_GUIDANCE",
    "SUBJECT_FINANCIAL_CONDITIONS",
    "PREDICATE_ASSESSMENT",
    "PREDICATE_RATIONALE",
    "PREDICATE_STATEMENT",
    "SUBJECT_MONETARY_POLICY",
    "SnbStatementExtractor",
]