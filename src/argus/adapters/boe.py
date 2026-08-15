from ..models import CentralBank
from .base import BankAdapter, html_source, rss_source

_MPR_PC_TRANSCRIPT_PAGES = (
    "https://www.bankofengland.co.uk/monetary-policy-report/2020/january-2020",
    "https://www.bankofengland.co.uk/monetary-policy-report/2020/november-2020",
    "https://www.bankofengland.co.uk/monetary-policy-report/2021/february-2021",
    "https://www.bankofengland.co.uk/monetary-policy-report/2021/may-2021",
    "https://www.bankofengland.co.uk/monetary-policy-report/2021/august-2021",
    "https://www.bankofengland.co.uk/monetary-policy-report/2021/november-2021",
    "https://www.bankofengland.co.uk/monetary-policy-report/2022/february-2022",
    "https://www.bankofengland.co.uk/monetary-policy-report/2022/may-2022",
    "https://www.bankofengland.co.uk/monetary-policy-report/2022/august-2022",
    "https://www.bankofengland.co.uk/monetary-policy-report/2022/november-2022",
    "https://www.bankofengland.co.uk/monetary-policy-report/2023/february-2023",
    "https://www.bankofengland.co.uk/monetary-policy-report/2023/may-2023",
    "https://www.bankofengland.co.uk/monetary-policy-report/2023/august-2023",
    "https://www.bankofengland.co.uk/monetary-policy-report/2023/november-2023",
    "https://www.bankofengland.co.uk/monetary-policy-report/2024/february-2024",
    "https://www.bankofengland.co.uk/monetary-policy-report/2024/may-2024",
    "https://www.bankofengland.co.uk/monetary-policy-report/2024/august-2024",
    "https://www.bankofengland.co.uk/monetary-policy-report/2024/november-2024",
    "https://www.bankofengland.co.uk/monetary-policy-report/2025/february-2025",
    "https://www.bankofengland.co.uk/monetary-policy-report/2025/may-2025",
    "https://www.bankofengland.co.uk/monetary-policy-report/2025/august-2025",
    "https://www.bankofengland.co.uk/monetary-policy-report/2025/november-2025",
    "https://www.bankofengland.co.uk/monetary-policy-report/2026/february-2026",
    "https://www.bankofengland.co.uk/monetary-policy-report/2026/april-2026",
    "https://www.bankofengland.co.uk/monetary-policy-report/2026/july-2026",
)


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
            html_source(
                "boe_mpc_press_conference",
                "boe",
                "BoE MPC press conference transcripts (MPR issue pages)",
                "https://www.bankofengland.co.uk/monetary-policy-report/2026/july-2026",
                priority=6,
                types=("press_conference",),
                include=(r"mpr-press-conference-transcript",),
                scope_prefixes=(
                    "https://www.bankofengland.co.uk/-/media/boe/files/monetary-policy-report/",
                ),
                keep_documents=True,
                pagination_urls=_MPR_PC_TRANSCRIPT_PAGES,
            ),
        ]
        return bank, sources