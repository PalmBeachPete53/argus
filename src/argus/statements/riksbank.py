"""Riksbank — Sveriges Riksbank monetary policy report extractor (Phase 4.2).

Encapsulates the Riksbank's vocabulary on top of the shared Phase 4.2 engine.
Riksbank language uses "per cent"; the reference date is the "Press release —
25 June 2026" style release date. Section headings mirror the Monetary Policy
Report structure (inflation, economic activity, the labour market, monetary
policy).
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


class RiksbankStatementExtractor(BankStatementExtractor):
    bank = "riksbank"
    extraction_version = EXTRACTION_VERSION

    intro_headings = frozenset({
        "sveriges riksbank",
        "monetary policy report",
        "press release",
        "report on monetary policy",
    })
    risk_headings = frozenset({"risks", "risk assessment", "risks and uncertainty", "uncertainty"})
    guidance_headings = frozenset({"monetary policy", "policy stance", "the policy rate path"})
    inflation_headings = frozenset({
        "inflation",
        "inflation in sweden",
        "inflation expectations",
        "cpif",
        "price developments",
    })
    growth_headings = frozenset({
        "economic activity",
        "economic outlook",
        "the swedish economy",
        "the economy",
        "development of demand",
    })
    labour_headings = frozenset({"the labour market", "labour market", "employment"})
    financial_headings = frozenset({"financial conditions", "financial markets"})

    guidance_anchors: tuple[re.Pattern, ...] = (
        re.compile(r"\bthe\s+policy\s+rate\s+is\s+expected\s+to\b", re.IGNORECASE),
        re.compile(r"\bthe\s+policy\s+rate\s+can\s+(?:also\s+)?be\s+(?:cut|lowered|adjusted)\b", re.IGNORECASE),
        re.compile(r"\briksbank\s+will\s+continue\s+to\b", re.IGNORECASE),
        re.compile(r"\bthe\s+riksbank\s+stands\s+ready\s+to\b", re.IGNORECASE),
        re.compile(r"\bwill\s+be\s+guided\s+by\b", re.IGNORECASE),
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
        re.compile(r"\bto\s+bring\s+inflation\s+back\s+to\s+target\b", re.IGNORECASE),
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
    "RiksbankStatementExtractor",
]