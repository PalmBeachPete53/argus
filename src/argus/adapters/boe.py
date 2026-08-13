from ..models import CentralBank
from .base import BankAdapter, html_source, rss_source


class BoEAdapter(BankAdapter):
    def _build(self):
        bank = CentralBank("boe", "Bank of England", "GBP", "bankofengland.co.uk")
        sources = [
            rss_source(
                "boe_news_rss",
                "boe",
                "BoE news RSS",
                "https://www.bankofengland.co.uk/rss/news",
                priority=1,
                types=("monetary_policy_decision", "monetary_policy_statement"),
            ),
            rss_source(
                "boe_publications_rss",
                "boe",
                "BoE publications RSS (MPR, MPC minutes)",
                "https://www.bankofengland.co.uk/rss/publications",
                priority=2,
            ),
            html_source(
                "boe_news_html",
                "boe",
                "BoE news listing (monetary policy items)",
                "https://www.bankofengland.co.uk/news",
                priority=5,
                include=(r"/(monetary-policy-summary-and-minutes|monetary-policy-report)/",),
                scope_prefixes=("https://www.bankofengland.co.uk/",),
            ),
        ]
        return bank, sources