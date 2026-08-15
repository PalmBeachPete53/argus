"""RBA — Reserve Bank of Australia statement extractor (Phase 6).

Encapsulates the RBA's "Statement by the Reserve Bank Board: Monetary Policy
Decision" / "Statement on Monetary Policy" vocabulary on top of the shared
Phase 6 engine. RBA language uses "per cent"; the release date is the "Date:
4 August 2026" header.
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


class RbaStatementExtractor(BankStatementExtractor):
    bank = "rba"
    extraction_version = EXTRACTION_VERSION

    intro_headings = frozenset({
        "statement by the reserve bank board",
        "monetary policy decision",
        "reserve bank of australia",
        "press release",
    })
    risk_headings = frozenset({"risks", "risk assessment", "risks around the outlook", "key uncertainties"})
    guidance_headings = frozenset({"forward guidance", "guidance", "policy stance", "monetary policy stance"})
    inflation_headings = frozenset({
        "inflation",
        "inflation outlook",
        "headline inflation",
        "underlying inflation",
        "prices",
    })
    growth_headings = frozenset({
        "economic outlook",
        "the economic outlook",
        "economic activity",
        "the economy",
        "output",
    })
    labour_headings = frozenset({"the labour market", "labour market", "employment"})
    financial_headings = frozenset({"financial conditions", "financial markets"})

    guidance_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\bthe\s+board\s+is\s+not\s+ruling\s+anything\s+(?:in|out)\b", re.IGNORECASE),
        re.compile(r"\bthe\s+board\s+will\s+make\s+its\s+decisions\b", re.IGNORECASE),
        re.compile(r"\bwill\s+depend\s+on\b", re.IGNORECASE),
        re.compile(r"\bthe\s+board\s+will\s+continue\s+to\b", re.IGNORECASE),
        re.compile(r"\bevidence-based\b", re.IGNORECASE),
        re.compile(r"\bdata-dependent\b", re.IGNORECASE),
        re.compile(r"\bstands?\s+ready\s+to\b", re.IGNORECASE),
    )

    risk_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\brisks?\s+(?:to|around|surrounding|from|of|around|are|were|remain|have|has|posed|associated)\b", re.IGNORECASE),
        re.compile(r"\b(?:downside|upside|two-sided|symmetric|broadly\s+balanced)\s+risks?\b", re.IGNORECASE),
        re.compile(r"\btilted\b", re.IGNORECASE),
        re.compile(r"\buncertain(?:ty|ties)?\b", re.IGNORECASE),
    )

    rationale_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\bto\s+get\s+inflation\s+back\s+to\s+target\b", re.IGNORECASE),
        re.compile(r"\bto\s+bring\s+inflation\s+back\b", re.IGNORECASE),
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
    "RbaStatementExtractor",
]