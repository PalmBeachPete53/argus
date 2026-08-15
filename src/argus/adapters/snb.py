from ..models import CentralBank
from .base import BankAdapter, html_source, rss_source

_SUMMARIES_PAGE = "https://www.snb.ch/en/publications/communication/summaries"


class SNBAdapter(BankAdapter):
    def _build(self):
        bank = CentralBank("snb", "Swiss National Bank", "CHF", "snb.ch")
        sources = [
            rss_source(
                "snb_mopo_rss",
                "snb",
                "SNB monetary policy RSS",
                "https://www.snb.ch/public/rss/en/mopo",
                priority=1,
                types=("monetary_policy_decision", "monetary_policy_assessment"),
            ),
            rss_source(
                "snb_pressrel_rss",
                "snb",
                "SNB press releases RSS",
                "https://www.snb.ch/public/rss/en/pressrel",
                priority=2,
            ),
            html_source(
                "snb_summaries",
                "snb",
                "SNB summaries of the monetary policy assessment discussion",
                _SUMMARIES_PAGE,
                priority=3,
                types=("minutes",),
                include=(r"zus_\d{8}",),
                scope_prefixes=("https://www.snb.ch/",),
            ),
            html_source(
                "snb_decision_archive",
                "snb",
                "SNB monetary policy decisions archive",
                "https://www.snb.ch/en/the-snb/mandates-goals/monetary-policy/decisions",
                priority=4,
                types=("monetary_policy_decision",),
                include=(r"(press-release)|(pre_\d{8})",),
                scope_prefixes=("https://www.snb.ch/",),
                allow_future=True,
            ),
        ]
        return bank, sources