from ..models import CentralBank
from .base import BankAdapter, html_source, rss_source


class BoCAdapter(BankAdapter):
    def _build(self):
        bank = CentralBank("boc", "Bank of Canada", "CAD", "bankofcanada.ca")
        sources = [
            rss_source(
                "boc_press_releases_rss",
                "boc",
                "BoC press releases RSS (RDF)",
                "https://www.bankofcanada.ca/content_type/press-releases/feed/",
                priority=1,
                types=("monetary_policy_decision", "policy_interest_rate"),
            ),
            rss_source(
                "boc_announcements_rss",
                "boc",
                "BoC announcements RSS",
                "https://www.bankofcanada.ca/content_type/announcements/feed/",
                priority=2,
            ),
            html_source(
                "boc_fad_archive",
                "boc",
                "BoC interest-rate / FAD press release archive",
                "https://www.bankofcanada.ca/press/press-releases/?category=interest-rates",
                priority=5,
                types=("monetary_policy_decision", "policy_interest_rate"),
                include=(r"fad-press-release", r"policy-interest-rate"),
                scope_prefixes=("https://www.bankofcanada.ca/",),
                pagination_urls=(
                    "https://www.bankofcanada.ca/press/press-releases/page/2/?category=interest-rates",
                    "https://www.bankofcanada.ca/press/press-releases/page/3/?category=interest-rates",
                    "https://www.bankofcanada.ca/press/press-releases/page/4/?category=interest-rates",
                    "https://www.bankofcanada.ca/press/press-releases/page/5/?category=interest-rates",
                ),
            ),
            html_source(
                "boc_key_interest_rate_schedule",
                "boc",
                "BoC fixed announcement dates schedule",
                "https://www.bankofcanada.ca/core-functions/monetary-policy/key-interest-rate/",
                priority=7,
                types=("monetary_policy_decision", "policy_interest_rate"),
                include=(r"2025|2026|2027",),
                scope_prefixes=("https://www.bankofcanada.ca/",),
                allow_future=True,
            ),
        ]
        return bank, sources