from ..models import CentralBank
from .base import BankAdapter, html_source, rss_source


class BoJAdapter(BankAdapter):
    def _build(self):
        bank = CentralBank("boj", "Bank of Japan", "JPY", "boj.or.jp")
        sources = [
            rss_source(
                "boj_whatsnew_rss",
                "boj",
                "BoJ 'what's new' RSS (EN)",
                "https://www.boj.or.jp/en/rss/whatsnew.xml",
                priority=1,
            ),
            html_source(
                "boj_mopo_archive",
                "boj",
                "BoJ monetary policy meetings archive",
                "https://www.boj.or.jp/en/mopo/mpmdeci/index.htm",
                priority=4,
                scope_prefixes=("https://www.boj.or.jp/en/mopo/",),
                include=(r"/mopo/", r"\.(htm|html)$"),
                exclude=(
                    r"mpmsche_minu",
                    r"/mpr_\d+/index\.htm$",
                    r"^https://www\.boj\.or\.jp/en/mopo/mpmdeci/index\.htm$",
                    r"\.pdf$",
                ),
                pagination_urls=(
                    "https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/index.htm",
                    "https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2025/index.htm",
                ),
            ),
        ]
        return bank, sources