"""Fed — FOMC statement extractor (Phase 4.2).

Encapsulates the Federal Reserve's own statement vocabulary (FOMC language) on
top of the shared Phase 4.2 engine. The FOMC statement is a single flowing text —
most sections route through the content-first fallback (guidance > risk >
rationale). Value claims use "percent"; the reference date is the "For release
at 2:00 p.m. EDT, September 20, 2026" header.
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


class FedStatementExtractor(BankStatementExtractor):
    bank = "fed"
    extraction_version = EXTRACTION_VERSION

    intro_headings = frozenset({
        "fomc statement",
        "federal reserve issues fomc statement",
        "monetary policy statement",
        "statement on longer-run goals and monetary policy strategy",
        "implementation note issued september",
        "federal reserve press release",
    })
    risk_headings = frozenset({"risk assessment", "risks to the economic outlook", "risks", "uncertainty"})
    guidance_headings = frozenset({
        "forward guidance",
        "guidance",
        "monetary policy outlook",
        "outlook for policy",
    })
    inflation_headings = frozenset({"inflation", "price stability", "price developments", "inflation outlook"})
    growth_headings = frozenset({
        "economic activity",
        "economic outlook",
        "recent economic developments",
        "the economic outlook",
        "real economic activity",
    })
    labour_headings = frozenset({"labor market", "labour market", "employment", "the labor market"})
    financial_headings = frozenset({"financial conditions", "financial market conditions", "financial markets"})

    guidance_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\bis\s+prepared\s+to\s+adjust\b", re.IGNORECASE),
        re.compile(r"\bstands?\s+ready\s+to\s+adjust\b", re.IGNORECASE),
        re.compile(r"\bwill\s+continue\s+to\s+(?:assess|monitor|adjust)\b", re.IGNORECASE),
        re.compile(r"\bwill\s+make\s+decisions\s+meeting\s+by\s+meeting\b", re.IGNORECASE),
        re.compile(r"\bdid not (?:see|expect) it will be appropriate\b", re.IGNORECASE),
        re.compile(r"\bdoes\s+not\s+expect\b", re.IGNORECASE),
        re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
        re.compile(r"\bwill\s+depend\s+on\b", re.IGNORECASE),
        re.compile(r"\bdata-dependent\b", re.IGNORECASE),
        re.compile(r"\bwill\s+remain\s+accommodative\b", re.IGNORECASE),
        re.compile(r"\bwill\s+not\s+be\s+appropriate\s+to\s+lower\b", re.IGNORECASE),
    )

    risk_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\brisks?\s+(?:to|for|around|surrounding|from|of|are|were|remain|remained|have|has|facing|posed|associated)\b", re.IGNORECASE),
        re.compile(r"\b(?:downside|upside|two-sided|symmetric|broadly\s+balanced)\s+risks?\b", re.IGNORECASE),
        re.compile(r"\btilted\b", re.IGNORECASE),
        re.compile(r"\buncertain(?:ty|ties)?\b", re.IGNORECASE),
    )

    rationale_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\bin (?:light|support) of\b", re.IGNORECASE),
        re.compile(r"\bto support\b", re.IGNORECASE),
        re.compile(r"\bconsistent with\b", re.IGNORECASE),
        re.compile(r"\bbased on\b", re.IGNORECASE),
        re.compile(r"\bin order to\b", re.IGNORECASE),
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
    "FedStatementExtractor",
]