from ..models import CentralBank
from .base import BankAdapter, html_source, rss_source, sitemap_source

_RBA_INT_RATE_YEARS = (
    "https://www.rba.gov.au/monetary-policy/int-rate-decisions/2026/",
    "https://www.rba.gov.au/monetary-policy/int-rate-decisions/2025/",
    "https://www.rba.gov.au/monetary-policy/int-rate-decisions/2024/",
    "https://www.rba.gov.au/monetary-policy/int-rate-decisions/2023/",
)


class RBAAdapter(BankAdapter):
    def _build(self):
        bank = CentralBank("rba", "Reserve Bank of Australia", "AUD", "rba.gov.au")
        sources = [
            rss_source(
                "rba_media_releases_rss",
                "rba",
                "RBA media releases RSS (incl. policy decisions)",
                "https://www.rba.gov.au/rss/rss-cb-media-releases.xml",
                priority=1,
                types=("monetary_policy_decision",),
            ),
            rss_source(
                "rba_smp_rss",
                "rba",
                "RBA Statement on Monetary Policy RSS",
                "https://www.rba.gov.au/rss/rss-cb-smp.xml",
                priority=2,
                types=("monetary_policy_report", "statement_on_monetary_policy"),
            ),
            html_source(
                "rba_int_rate_archive",
                "rba",
                "RBA monetary policy decisions archive",
                "https://www.rba.gov.au/monetary-policy/int-rate-decisions/",
                priority=4,
                types=("monetary_policy_decision",),
                include=(r"mr-\d{2}-\d{2}",),
                scope_prefixes=(
                    "https://www.rba.gov.au/monetary-policy/int-rate-decisions/",
                    "https://www.rba.gov.au/media-releases/",
                ),
                pagination_urls=_RBA_INT_RATE_YEARS,
            ),
            sitemap_source(
                "rba_sitemap_monetary",
                "rba",
                "RBA sitemap — monetary policy sections",
                "https://www.rba.gov.au/sitemap.xml",
                priority=6,
                include=(
                    r"/monetary-policy/int-rate-decisions/",
                    r"/media-releases/mr-\d{2}-\d{2}",
                    r"/publications/smp/",
                ),
            ),
        ]
        return bank, sources