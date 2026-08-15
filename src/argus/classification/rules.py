from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Pattern

from .base import HINT_CANONICAL


@dataclass(frozen=True)
class TypeRule:
    """A declarative rule mapping textual signals onto a publication type.

    ``url`` / ``title`` / ``content`` are tuples of case-insensitive regex
    patterns. ``banks`` restricts the rule to specific banks; ``exclude_banks``
    removes it from others. Leaving both empty makes the rule generic.
    """

    publication_type: str
    label: str
    url: tuple[str, ...] = ()
    title: tuple[str, ...] = ()
    content: tuple[str, ...] = ()
    banks: tuple[str, ...] | None = None
    exclude_banks: tuple[str, ...] = ()
    _compiled_url: tuple[Pattern, ...] = field(default=(), init=False, repr=False)
    _compiled_title: tuple[Pattern, ...] = field(default=(), init=False, repr=False)
    _compiled_content: tuple[Pattern, ...] = field(default=(), init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_compiled_url", tuple(self._compile(self.url)))
        object.__setattr__(self, "_compiled_title", tuple(self._compile(self.title)))
        object.__setattr__(self, "_compiled_content", tuple(self._compile(self.content)))

    @staticmethod
    def _compile(patterns) -> tuple[Pattern, ...]:
        compiled = []
        for pattern in patterns:
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error:
                continue
        return tuple(compiled)

    def applies_to(self, bank: str) -> bool:
        if self.banks is not None and bank not in self.banks:
            return False
        if bank in self.exclude_banks:
            return False
        return True

    def match_url(self, url: str) -> list[str]:
        return [p.pattern for p in self._compiled_url if p.search(url)]

    def match_title(self, title: str | None) -> list[str]:
        if not title:
            return []
        return [p.pattern for p in self._compiled_title if p.search(title)]

    def match_content(self, text: str | None) -> list[str]:
        if not text:
            return []
        return [p.pattern for p in self._compiled_content if p.search(text)]


def canonical_types(hints) -> list[str]:
    """Canonicalize raw type hints onto the vocabulary, preserving order."""
    seen: list[str] = []
    for hint in hints or ():
        if not hint:
            continue
        canonical = HINT_CANONICAL.get(str(hint), "other")
        if canonical not in seen:
            seen.append(canonical)
    return seen


# ---------------------------------------------------------------------------
# Generic rules (apply to every bank). Conservative on purpose: a false
# "unique" match here is worse than falling through to the next tier.
# ---------------------------------------------------------------------------

GENERIC_RULES: tuple[TypeRule, ...] = (
    TypeRule(
        "monetary_policy_decision",
        "decision",
        url=(
            r"monetary-policy-decisions?[/]",
            r"int-rate-decisions",
            r"policy( |-)rate-decision",
            r"policy-decisions?[/]",
        ),
        title=(
            r"monetary policy decisions?",
            r"policy( |-)?rate decision",
            r"interest( |-)?rate decision",
            r"policy( |-)?rate (maintained|held|kept|unchanged|raised|lowered)",
            r"monetary policy summary",
        ),
        content=(
            r"the governing council decided",
            r"the committee decided to",
            r"monetary policy decisions",
            r"\b(kept|set|maintained|held|raised|lowered) the policy rate\b",
            r"policy rate (was )?(left|kept|held) (at|unchanged)",
        ),
    ),
    TypeRule(
        "monetary_policy_statement",
        "statement",
        url=(r"monetary-policy-statement",),
        title=(r"monetary policy statement",),
        content=(r"this monetary policy statement",),
        exclude_banks=("rba", "rbnz"),
    ),
    TypeRule(
        "press_conference",
        "press_conference",
        url=(r"press[_-]conference",),
        title=(r"press conference",),
        content=(r"welcome to (the )?press conference",),
    ),
    TypeRule(
        "minutes",
        "minutes",
        url=(r"fomcminutes", r"minutes-of-", r"[^a-z]minutes[^a-z]", r"minutes[.]pdf"),
        title=(
            r"minutes of",
            r"monetary policy minutes",
            r"\bmpc minutes\b",
        ),
        content=(
            r"minutes of the (monetary policy committee|federal open market committee)",
            r"the survey of economic projections",
        ),
    ),
    TypeRule(
        "meeting_account",
        "meeting_account",
        url=(r"/accounts[/]|[^a-z]accounts[^a-z]",),
        title=(
            r"account of the monetary",
            r"meeting account",
            r"account of the (governing council|monetary policy meeting)",
        ),
        content=(r"account of the monetary policy meeting",),
    ),
    TypeRule(
        "economic_projections",
        "economic_projections",
        url=(r"fomcprojtabl", r"economic-projection", r"summary-of-economic-projections"),
        title=(
            r"(summary of )?economic projections",
            r"outlook for economic activity and prices",
        ),
        content=(r"summary of economic projections",),
    ),
    TypeRule(
        "monetary_policy_report",
        "monetary_policy_report",
        url=(
            r"monetary-policy-report",
            r"mpr[_-]\d{4}",
            r"inflation-report",
            r"publications[^a-z]*smp",
            r"/smp[/]",
        ),
        title=(
            r"monetary policy report",
            r"inflation report",
            r"statement on monetary policy",
        ),
        content=(
            r"monetary policy report",
            r"this monetary policy report",
        ),
        exclude_banks=("boj",),
    ),
    TypeRule(
        "speech",
        "speech",
        url=(r"speeches?[/]", r"-speech[^a-z]", r"^speech"),
        title=(r"\bspeech\b", r"\bremarks\b"),
        content=(r"i am delighted to (be here|speak)", r"thank you[^.]*for the opportunity to speak"),
    ),
    TypeRule(
        "interview",
        "interview",
        url=(r"interview",),
        title=(r"\binterview\b",),
    ),
)


DEFAULT_RULES: tuple[TypeRule, ...] = GENERIC_RULES


def rules_for(bank: str, rules: tuple[TypeRule, ...] | None = None) -> list[TypeRule]:
    base = list(rules) if rules is not None else list(GENERIC_RULES)
    return [rule for rule in base if rule.applies_to(bank)]