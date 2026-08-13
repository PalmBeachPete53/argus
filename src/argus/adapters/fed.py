from ..models import CentralBank
from .base import BankAdapter, html_source, rss_source, sitemap_source

FED_MONETARY_PREFIXES = (
    "https://www.federalreserve.gov/monetarypolicy/",
    "https://www.federalreserve.gov/newsevents/pressreleases/",
)


class FedAdapter(BankAdapter):
    def _build(self):
        bank = CentralBank("fed", "Federal Reserve", "USD", "federalreserve.gov")
        sources = [
            rss_source(
                "fed_monetary_press_rss",
                "fed",
                "FOMC press releases RSS",
                "https://www.federalreserve.gov/feeds/press_monetary.xml",
                priority=1,
                types=("monetary_policy_decision", "monetary_policy_statement"),
            ),
            rss_source(
                "fed_press_releases_rss",
                "fed",
                "Fed press releases RSS (all categories)",
                "https://www.federalreserve.gov/feeds/press_all.xml",
                priority=2,
            ),
            html_source(
                "fed_fomc_calendar",
                "fed",
                "FOMC calendar and meeting materials",
                "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                priority=4,
                types=("monetary_policy_decision", "minutes", "projections"),
                include=(
                    r"/(monetarypolicy|newsevents)/",
                    r"monetary|fomc",
                ),
                exclude=(
                    r"\.pdf$",
                    r"index\.htm$",
                    r"\?title=",
                ),
                scope_prefixes=FED_MONETARY_PREFIXES,
                allow_future=True,
            ),
        ]
        return bank, sources