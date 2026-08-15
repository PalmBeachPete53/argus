from __future__ import annotations

from .rules import TypeRule

# ---------------------------------------------------------------------------
# Bank-specific classification knowledge. Kept in a single declarative place:
# the generic engine in ``classifier.py`` never branches on bank ids.
# ---------------------------------------------------------------------------

BANK_RULES: dict[str, tuple[TypeRule, ...]] = {
    "fed": (
        TypeRule(
            "monetary_policy_decision",
            "fed",
            url=(r"pressreleases/monetary\d{8}", r"monetarypolicy/monetary\d{8}"),
            title=(r"issues fomc statement", r"fomc statement", r"monetary policy statement"),
        ),
        TypeRule(
            "minutes",
            "fed",
            url=(r"fomcminutes",),
            title=(r"minutes of the federal open market committee",),
        ),
        TypeRule(
            "economic_projections",
            "fed",
            url=(r"fomcprojtabl",),
            title=(r"projections", r"summary of economic projections"),
        ),
        TypeRule(
            "press_conference",
            "fed",
            url=(r"presconf", r"press-?conference"),
            title=(
                r"press conference",
                r"(?:chair|chairman|chairwoman)[!'’]?s?\s+press\s+conference",
            ),
        ),
    ),
    "ecb": (
        TypeRule(
            "meeting_account",
            "ecb",
            url=(r"press/accounts[/]",),
            title=(r"account of the monetary policy meeting",),
        ),
        TypeRule(
            "press_conference",
            "ecb",
            url=(r"press_conference",),
            title=(r"press conference",),
        ),
        TypeRule(
            "monetary_policy_decision",
            "ecb",
            url=(r"press/govcdec[/]",),
        ),
    ),
    "boe": (
        TypeRule(
            "monetary_policy_decision",
            "boe",
            url=(r"monetary-policy-summary-and-minutes",),
            title=(r"monetary policy summary and minutes",),
            content=(r"bank rate (at|maintained|raised|reduced)", r"the mpc voted"),
        ),
        TypeRule(
            "minutes",
            "boe",
            url=(r"monetary-policy-summary-and-minutes[^/]*/[^/]*", r"minutes-of-the-mpc"),
            title=(r"minutes of the (monetary policy committee|mpc)",),
            content=(r"minutes of the monetary policy committee",),
        ),
        TypeRule(
            "monetary_policy_report",
            "boe",
            url=(r"monetary-policy-report",),
            title=(r"monetary policy report",),
        ),
    ),
    "boj": (
        TypeRule(
            "monetary_policy_statement",
            "boj",
            url=(r"statement_on_monetary_policy",),
            title=(r"statement on monetary policy",),
        ),
        TypeRule(
            "economic_projections",
            "boj",
            url=(r"outlook-for-economic-activity", r"outlook_report", r"mpr_\d{4}"),
            title=(r"outlook for economic activity and prices",),
        ),
        TypeRule(
            "minutes",
            "boj",
            url=(r"/mopo/minutes[/]",),
            title=(r"minutes of the monetary policy meeting",),
        ),
    ),
    "snb": (
        TypeRule(
            "monetary_policy_decision",
            "snb",
            url=(r"pre_\d{8}",),
            title=(r"monetary policy assessment of \d{1,2} ",),
            content=(r"swiss national bank (is|keeps|maintains|raises|lowers) the (snb|policy) rate",),
        ),
        TypeRule(
            "minutes",
            "snb",
            url=(r"zus_\d{8}",),
            title=(
                r"summary of discussion",
                r"summary of (the )?monetary policy assessment discussion",
            ),
        ),
    ),
    "boc": (
        TypeRule(
            "monetary_policy_decision",
            "boc",
            url=(r"fad-press-release", r"policy-interest-rate"),
            title=(r"fad press release",),
        ),
        TypeRule(
            "monetary_policy_report",
            "boc",
            url=(r"monetary-policy-report",),
            title=(r"monetary policy report",),
        ),
    ),
    "rba": (
        TypeRule(
            "monetary_policy_report",
            "rba",
            url=(r"statement-on-monetary-policy", r"publications[^a-z]*smp", r"/smp[/]"),
            title=(r"statement on monetary policy",),
        ),
        TypeRule(
            "monetary_policy_decision",
            "rba",
            url=(r"int-rate-decisions",),
            title=(r"monetary policy decision", r"cash rate decision"),
        ),
    ),
    "rbnz": (
        TypeRule(
            "monetary_policy_report",
            "rbnz",
            url=(r"monetary-policy-statement",),
            title=(r"monetary policy statement",),
        ),
        TypeRule(
            "monetary_policy_decision",
            "rbnz",
            url=(r"monetary-policy-decisions", r"ocr"),
            title=(r"monetary policy decision", r"ocr decision"),
        ),
    ),
    "norges": (
        TypeRule(
            "monetary_policy_decision",
            "norges",
            url=(r"policy-rate-decision",),
            title=(r"policy rate decision", r"monetary policy decision"),
        ),
        TypeRule(
            "monetary_policy_report",
            "norges",
            url=(r"monetary-policy-report",),
            title=(r"monetary policy report",),
        ),
        TypeRule(
            "minutes",
            "norges",
            title=(r"summary of the committee['’]?s (assessment|deliberations)",),
        ),
    ),
    "riksbank": (
        TypeRule(
            "monetary_policy_decision",
            "riksbank",
            url=(r"monetary-policy-decisions?", r"policy-rate-decision"),
            title=(r"monetary policy decision", r"policy rate decision"),
        ),
        TypeRule(
            "minutes",
            "riksbank",
            url=(r"minutes",),
            title=(r"minutes of the (executive board|monetary policy)",),
        ),
    ),
}


def rules_for_bank(bank: str) -> tuple[TypeRule, ...]:
    return BANK_RULES.get(bank, ())