from ..models import CentralBank
from .base import BankAdapter, rss_source, sitemap_source


class ECBAdapter(BankAdapter):
    def _build(self):
        bank = CentralBank("ecb", "European Central Bank", "EUR", "ecb.europa.eu")
        sources = [
            rss_source(
                "ecb_press_rss",
                "ecb",
                "ECB press releases RSS (incl. monetary policy decisions)",
                "https://www.ecb.europa.eu/rss/press.html",
                priority=1,
                types=("monetary_policy_decision", "press_release"),
                include=(r"/press/(pr/date|govcdec|press_conference)/",),
            ),
            rss_source(
                "ecb_publications_rss",
                "ecb",
                "ECB publications RSS (accounts, reports, bulletins)",
                "https://www.ecb.europa.eu/rss/pub.html",
                priority=2,
            ),
            sitemap_source(
                "ecb_sitemap_monetary",
                "ecb",
                "ECB sitemap — monetary policy sections",
                "https://www.ecb.europa.eu/sitemap.xml",
                priority=6,
                include=(
                    r"https://www\.ecb\.europa\.eu/press/(pr/date|accounts|govcdec|press_conference)/",
                    r"https://www\.ecb\.europa\.eu/mopo/decisions/",
                ),
                exclude=(r"\.pdf$",),
            ),
        ]
        return bank, sources