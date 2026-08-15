"""BoE — Bank of England statement extractor (Phase 6).

Encapsulates the Bank of England's Monetary Policy Committee vocabulary on top
of the shared Phase 6 engine. BoE language uses "per cent"; the reference date
is the "20 August 2026" style release date. Sections mirror the Monetary Policy
Report structure: inflation outlook, economic outlook, the labour market, wages
and costs, risks to the outlook.
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


class BoeStatementExtractor(BankStatementExtractor):
    bank = "boe"
    extraction_version = EXTRACTION_VERSION

    intro_headings = frozenset({"monetary policy summary", "summary", "bank of england"})
    risk_headings = frozenset({"risks to the outlook", "risk assessment", "risks", "the risks"})
    guidance_headings = frozenset({"forward guidance", "guidance", "policy stance", "monetary policy stance"})
    inflation_headings = frozenset({"inflation outlook", "inflation", "cpi inflation", "price developments"})
    growth_headings = frozenset({
        "the economic outlook",
        "economic outlook",
        "economic activity",
        "the outlook for activity",
        "global activity",
        "demand",
    })
    labour_headings = frozenset({
        "the labour market",
        "labour market",
        "employment",
        "wages and costs",
        "pay growth",
        "unemployment",
    })
    financial_headings = frozenset({"financial conditions", "monetary conditions", "financial markets"})

    guidance_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\bthe\s+committee\s+will\s+(?:continue\s+to\s+)?(?:monitor|assess|keep\s+under\s+review)\b", re.IGNORECASE),
        re.compile(r"\bmonetary\s+policy\s+needs\s+to\s+be\b", re.IGNORECASE),
        re.compile(r"\bstands?\s+ready\s+to\b", re.IGNORECASE),
        re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
        re.compile(r"\bwill\s+depend\s+on\b", re.IGNORECASE),
        re.compile(r"\bdata-dependent\b", re.IGNORECASE),
        re.compile(r"\bpolicy\s+will\s+be\s+set\s+accordingly\b", re.IGNORECASE),
        re.compile(r"\bto\s+ensure\s+that\s+inflation\s+returns\s+sustainably\b", re.IGNORECASE),
        re.compile(r"\bdoes\s+not\s+pre-?commit\b", re.IGNORECASE),
    )

    risk_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\brisks?\s+(?:to|around|surrounding|from|of|are|were|remain|remain|have|has|facing|associated)\b", re.IGNORECASE),
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
    "BoeStatementExtractor",
]