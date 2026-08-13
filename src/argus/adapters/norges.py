from ..models import CentralBank
from .base import BankAdapter, rss_source, sitemap_source


class NorgesBankAdapter(BankAdapter):
    def _build(self):
        bank = CentralBank("norges", "Norges Bank", "NOK", "norges-bank.no")
        sources = [
            rss_source(
                "norges_press_releases_rss",
                "norges",
                "Norges Bank press releases RSS (incl. policy-rate decisions)",
                "https://www.norges-bank.no/en/rss-feeds/Press-releases---Norges-Bank/",
                priority=1,
                types=("monetary_policy_decision",),
            ),
            rss_source(
                "norges_mpr_rss",
                "norges",
                "Norges Bank Monetary Policy Report RSS",
                "https://www.norges-bank.no/en/rss-feeds/Norges-Bank-Monetary-Policy-Report-with-financial-stability-assessment/",
                priority=2,
                types=("monetary_policy_report",),
            ),
            sitemap_source(
                "norges_sitemap_monetary",
                "norges",
                "Norges Bank sitemap — monetary policy sections",
                "https://www.norges-bank.no/sitemap.xml",
                priority=6,
                include=(r"monetary-policy-meetings|monetary-policy-report",),
            ),
        ]
        return bank, sources