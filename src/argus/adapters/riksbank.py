from ..models import CentralBank
from .base import BankAdapter, rss_source, sitemap_source


class RiksbankAdapter(BankAdapter):
    def _build(self):
        bank = CentralBank("riksbank", "Sveriges Riksbank", "SEK", "riksbank.se")
        sources = [
            rss_source(
                "riksbank_press_releases_rss",
                "riksbank",
                "Riksbank press releases RSS (incl. policy decisions)",
                "https://www.riksbank.se/en-gb/rss/press-releases/",
                priority=1,
            ),
            rss_source(
                "riksbank_minutes_rss",
                "riksbank",
                "Riksbank MPC minutes RSS",
                "https://www.riksbank.se/en-gb/rss/minutes-of-the-executive-boards-monetary-policy-meetings/",
                priority=2,
                types=("minutes",),
            ),
            sitemap_source(
                "riksbank_sitemap_monetary",
                "riksbank",
                "Riksbank sitemap — monetary policy sections",
                "https://www.riksbank.se/sitemap.xml",
                priority=6,
                include=(r"/en-gb/monetary-policy/",),
                exclude=(r"\.pdf$",),
            ),
        ]
        return bank, sources