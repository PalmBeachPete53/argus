from ..models import CentralBank
from .base import BankAdapter, html_source, rss_source, sitemap_source

_RBA_INT_RATE_YEARS = (
    "https://www.rba.gov.au/monetary-policy/int-rate-decisions/2026/",
    "https://www.rba.gov.au/monetary-policy/int-rate-decisions/2025/",
    "https://www.rba.gov.au/monetary-policy/int-rate-decisions/2024/",
    "https://www.rba.gov.au/monetary-policy/int-rate-decisions/2023/",
)


_BOARD_MINUTES_PATH = "https://www.rba.gov.au/monetary-policy/rba-board-minutes/"

# The RBA publishes each set of Board Minutes at a dated leaf under the year
# archive; the ISO form (2015+, e.g. 2026-06-16) and the pre-2015 ddmmyyyy form
# (e.g. 04112014) are both canonical.
_RBA_BOARD_MINUTES_LEAF = r"rba-board-minutes/\d{4}/(?:\d{4}-\d{2}-\d{2}|\d{8})\.html"

# Recent completion years are crawled page by page; the index page links to the
# older year archives which keep the pre-2015 filename convention.
_RBA_BOARD_MINUTES_YEARS = tuple(
    f"{_BOARD_MINUTES_PATH}{year}/" for year in range(2026, 2005, -1)
)

# The archive root page carries a "latest Minutes" anchor whose surrounding
# context has no date of its own (so the newest minute would inherit a stale
# date from the page chrome). The year archives anchor every leaf with its own
# date, so the crawl starts on the newest year instead and stays self-consistent.
_RBA_BOARD_MINUTES_ROOT = f"{_BOARD_MINUTES_PATH}2026/"


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
                # Search Discovery fallback: rba.gov.au blocks automated native
                # access from some environments (HTTP 403). The query is
                # constrained to the official domain and the decision wording.
                search_query='site:rba.gov.au "Monetary Policy Decision"',
                search_domain="rba.gov.au",
                search_engines=(),
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
            html_source(
                "rba_board_minutes_archive",
                "rba",
                "RBA Board Minutes archive",
                _RBA_BOARD_MINUTES_ROOT,
                priority=7,
                types=("minutes",),
                include=(_RBA_BOARD_MINUTES_LEAF,),
                scope_prefixes=(_BOARD_MINUTES_PATH,),
                pagination_urls=_RBA_BOARD_MINUTES_YEARS,
            ),
        ]
        return bank, sources