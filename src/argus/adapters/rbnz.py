from ..models import CentralBank
from .base import BankAdapter, html_source, sitemap_source


class RBNZAdapter(BankAdapter):
    def _build(self):
        bank = CentralBank("rbnz", "Reserve Bank of New Zealand", "NZD", "rbnz.govt.nz")
        sources = [
            html_source(
                "rbnz_ocr_decisions",
                "rbnz",
                "RBNZ OCR decision timeline (media releases + MPS)",
                "https://www.rbnz.govt.nz/monetary-policy/monetary-policy-decisions",
                priority=1,
                include=(
                    r"/(news|publications)/",
                    r"media-release|monetary-policy-statement",
                ),
                exclude=(r"\.pdf$",),
                scope_prefixes=("https://www.rbnz.govt.nz/",),
                # Search Discovery fallback: rbnz.govt.nz blocks automated native
                # access from some environments (HTTP 403). The query is
                # constrained to the official domain and the MPS wording.
                search_query='site:rbnz.govt.nz "Monetary Policy Statement"',
                search_domain="rbnz.govt.nz",
                search_engines=(),
            ),
            sitemap_source(
                "rbnz_sitemap_monetary",
                "rbnz",
                "RBNZ sitemap — monetary policy sections",
                "https://www.rbnz.govt.nz/sitemap.xml",
                priority=6,
                include=(
                    r"/monetary-policy/",
                    r"/publications/monetary-policy-statement/",
                ),
                exclude=(r"\.pdf$",),
            ),
        ]
        return bank, sources