#!/usr/bin/env python3
"""Capture a deterministic golden corpus from real official central-bank sources.

Phase E — the golden corpus is built by CAPTURING real official sources once,
storing the artifacts under ``tests/golden/<bank>/`` with a provenance manifest
(``tests/golden/manifest.json``). The offline test suite (``tests/test_golden_corpus.py``)
then replays these captured artifacts through the shared L4 harness without any
network access.

Run:
    python scripts/capture_golden.py

Only the capture step touches the network. Re-running the capture is safe:
existing files are left untouched unless ``--overwrite`` is given, so a changed
official page never silently alters the versioned corpus.

Each entry records provenance:
    bank, source_id, source_url (discovery), publication_url, expected_type,
    entry_point, extractor, captured_at, discovery_sha256, document_sha256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "golden"
MANIFEST = GOLDEN / "manifest.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
TIMEOUT = 30

# One representative golden case per bank. ``url_contains`` selects the target
# publication from the real discovery artifact. ``entry_point`` / ``extractor``
# name the L4 family entry point and extractor used by the offline test.
GOLDEN_CASES = {
    "fed": {
        "source_id": "fed_monetary_press_rss",
        "discovery_url": "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "url_contains": "monetary20260729a.htm",
        "expected_type": "monetary_policy_decision",
        "entry_point": "extract_decision",
        "extractor": "FedDecisionExtractor",
    },
    "ecb": {
        "source_id": "ecb_press_rss",
        "discovery_url": "https://www.ecb.europa.eu/rss/press.html",
        "url_contains": "ecb.mp260723",
        "expected_type": "monetary_policy_decision",
        "entry_point": "extract_decision",
        "extractor": "EcbDecisionExtractor",
    },
    "boe": {
        "source_id": "boe_news_rss",
        "discovery_url": "https://www.bankofengland.co.uk/rss/news",
        "url_contains": "monetary-policy-summary-and-minutes/2026/july-2026",
        "expected_type": "monetary_policy_decision",
        "entry_point": "extract_decision",
        "extractor": "BoeDecisionExtractor",
    },
    "snb": {
        "source_id": "snb_mopo_rss",
        "discovery_url": "https://www.snb.ch/public/rss/en/mopo",
        "url_contains": "pre_20260618",
        "expected_type": "monetary_policy_decision",
        "entry_point": "extract_decision",
        "extractor": "SnbDecisionExtractor",
    },
    "boc": {
        "source_id": "boc_press_releases_rss",
        "discovery_url": "https://www.bankofcanada.ca/content_type/press-releases/feed/",
        "url_contains": "fad-press-release-2026-07-15",
        "expected_type": "monetary_policy_decision",
        "entry_point": "extract_decision",
        "extractor": "BocDecisionExtractor",
    },
    "norges": {
        "source_id": "norges_press_releases_rss",
        "discovery_url": "https://www.norges-bank.no/en/rss-feeds/Press-releases---Norges-Bank/",
        "url_contains": "2026-08-13-rate",
        "expected_type": "monetary_policy_decision",
        "entry_point": "extract_decision",
        "extractor": "NorgesDecisionExtractor",
    },
    "riksbank": {
        "source_id": "riksbank_press_releases_rss",
        "discovery_url": "https://www.riksbank.se/en-gb/rss/press-releases/",
        "url_contains": "policy-rate-unchanged-at-",
        "expected_type": "monetary_policy_decision",
        "entry_point": "extract_decision",
        "extractor": "RiksbankDecisionExtractor",
    },
    "boj": {
        "source_id": "boj_whatsnew_rss",
        "discovery_url": "https://www.boj.or.jp/en/rss/whatsnew.xml",
        "url_contains": "mpr_2026/k260731a.pdf",
        "expected_type": "monetary_policy_statement",
        "entry_point": "extract_statement",
        "extractor": "BojStatementExtractor",
        "document_kind": "pdf",
    },
    # RBA: native access from this environment is WAF-flaky; the reliable
    # discovery path is the Search Discovery fallback (SearXNG), which returns
    # official decision URLs. Capture the SearXNG JSON response as the discovery
    # artifact (discovery_mode="search").
    "rba": {
        "source_id": "rba_media_releases_rss",
        "discovery_url": "https://www.rba.gov.au/rss/rss-cb-media-releases.xml",
        "discovery_mode": "search",
        "searxng_base_url": "http://localhost:8080/",
        "search_query": 'site:rba.gov.au "Monetary Policy Decision"',
        "url_contains": "mr-26-19",
        # The RBA WAF blocks browser-like user agents but allows the honest
        # Argus collector UA (verified experimentally).
        "user_agent": "ArgusCollector/0.1 (official central bank publication collection; contact: argus@example.invalid)",
        "expected_type": "monetary_policy_decision",
        "entry_point": "extract_decision",
        "extractor": "RbaDecisionExtractor",
        "document_kind": "html",
    },
    "rbnz": {
        "source_id": "rbnz_ocr_decisions",
        "discovery_url": "https://www.rbnz.govt.nz/monetary-policy/monetary-policy-decisions",
        "url_contains": "",
        "expected_type": "monetary_policy_report",
        "entry_point": "extract_report",
        "extractor": "RbnzReportExtractor",
        "document_kind": "html",
    },
}


def _get(url: str, user_agent: str = UA) -> requests.Response:
    return requests.get(url, headers={"User-Agent": user_agent}, timeout=TIMEOUT)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _find_url(data: str, needle: str) -> str | None:
    """Find the first absolute URL in the discovery artifact containing ``needle``."""
    for candidate in re.findall(r"https?://[^\s\"'<>]+", data):
        candidate = candidate.rstrip("])}")
        if needle in candidate:
            return candidate
    return None


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def capture(bank: str, cfg: dict, *, overwrite: bool) -> dict | None:
    document_kind = cfg.get("document_kind", "html")
    document_ext = "pdf" if document_kind == "pdf" else "html"
    discovery_mode = cfg.get("discovery_mode", "native")
    discovery_ext = "json" if discovery_mode == "search" else "xml"
    discovery_fixture = f"{bank}/discovery.{discovery_ext}"
    document_fixture = f"{bank}/document.{document_ext}"
    discovery_path = GOLDEN / discovery_fixture
    document_path = GOLDEN / document_fixture
    if discovery_path.exists() and not overwrite:
        print(f"[{bank}] discovery already captured (use --overwrite to refresh)")
        return None

    if discovery_mode == "search":
        # Discovery via the Search Discovery fallback: query a SearXNG instance
        # and capture its JSON response (the offline golden test replays it
        # through SearchDiscovery → publication candidate → Fetcher).
        from urllib.parse import urlencode, urljoin

        base = cfg.get("searxng_base_url", "http://localhost:8080/").rstrip("/") + "/"
        query = cfg.get("search_query") or (cfg["query"] if "query" in cfg else "")
        search_url = urljoin(base, "search") + "?" + urlencode({"q": query, "format": "json"})
        resp = _get(search_url, cfg.get("user_agent", UA))
        if resp.status_code != 200:
            print(f"[{bank}] SKIPPED: searxng HTTP {resp.status_code}")
            return None
        discovery_bytes = resp.content
        target_url = _find_url(discovery_bytes.decode("utf-8", errors="replace"), cfg["url_contains"])
    else:
        resp = _get(cfg["discovery_url"], cfg.get("user_agent", UA))
        if resp.status_code != 200:
            print(f"[{bank}] SKIPPED: discovery HTTP {resp.status_code}")
            return None
        discovery_bytes = resp.content
        target_url = _find_url(discovery_bytes.decode("utf-8", errors="replace"), cfg["url_contains"])
    if target_url is None:
        print(f"[{bank}] SKIPPED: target publication not found in discovery artifact")
        return None

    doc_resp = _get(target_url, cfg.get("user_agent", UA))
    if doc_resp.status_code != 200:
        print(f"[{bank}] SKIPPED: document HTTP {doc_resp.status_code}")
        return None
    document_bytes = doc_resp.content

    _write(discovery_path, discovery_bytes)
    _write(document_path, document_bytes)

    entry = {
        "bank": bank,
        "source_id": cfg["source_id"],
        "source_url": cfg["discovery_url"],
        "publication_url": target_url,
        "expected_type": cfg["expected_type"],
        "entry_point": cfg["entry_point"],
        "extractor": cfg["extractor"],
        "discovery_fixture": discovery_fixture,
        "document_fixture": document_fixture,
        "discovery_mode": discovery_mode,
        "document_content_type": "application/pdf" if document_kind == "pdf" else "text/html",
        "discovery_sha256": _sha256(discovery_bytes),
        "document_sha256": _sha256(document_bytes),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    print(f"[{bank}] captured {target_url} ({len(document_bytes)} bytes)")
    return entry


def capture_manual(
    bank: str,
    cfg: dict,
    *,
    publication_url: str,
    discovery_file: Path,
    document_file: Path,
    overwrite: bool,
) -> dict | None:
    """Ingest an operator-downloaded official capture (an authorized environment
    downloads the official files; this step validates + versions them). The
    discovery file extension is preserved (``.json`` = Search Discovery capture,
    ``.xml``/``.html`` = native discovery capture)."""
    document_kind = cfg.get("document_kind", "html")
    document_ext = "pdf" if document_kind == "pdf" else "html"
    discovery_ext = "json" if discovery_file.suffix.lower() == ".json" else "xml"
    discovery_mode = "search" if discovery_ext == "json" else "native"
    discovery_fixture = f"{bank}/discovery.{discovery_ext}"
    document_fixture = f"{bank}/document.{document_ext}"
    if (GOLDEN / discovery_fixture).exists() and not overwrite:
        print(f"[{bank}] discovery already captured (use --overwrite to refresh)")
        return None
    discovery_bytes = discovery_file.read_bytes()
    document_bytes = document_file.read_bytes()
    if not discovery_bytes or not document_bytes:
        print(f"[{bank}] SKIPPED: empty discovery or document file")
        return None
    _write(GOLDEN / discovery_fixture, discovery_bytes)
    _write(GOLDEN / document_fixture, document_bytes)
    entry = {
        "bank": bank,
        "source_id": cfg["source_id"],
        "source_url": cfg["discovery_url"],
        "publication_url": publication_url,
        "expected_type": cfg["expected_type"],
        "entry_point": cfg["entry_point"],
        "extractor": cfg["extractor"],
        "discovery_fixture": discovery_fixture,
        "document_fixture": document_fixture,
        "discovery_mode": discovery_mode,
        "document_content_type": "application/pdf" if document_kind == "pdf" else "text/html",
        "discovery_sha256": _sha256(discovery_bytes),
        "document_sha256": _sha256(document_bytes),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "capture_method": "manual",
    }
    print(f"[{bank}] manual capture ingested: {publication_url} ({len(document_bytes)} bytes)")
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture the Argus golden corpus.")
    parser.add_argument("--overwrite", action="store_true", help="re-fetch existing captures")
    parser.add_argument("--manual", metavar="BANK", help="ingest an operator-downloaded capture for BANK")
    parser.add_argument("--publication-url", help="official publication URL (manual capture)")
    parser.add_argument("--discovery-file", type=Path, help="downloaded official discovery artifact (manual capture)")
    parser.add_argument("--document-file", type=Path, help="downloaded official document (manual capture)")
    parser.add_argument("banks", nargs="*", help="restrict capture to these banks")
    args = parser.parse_args()

    manifest = {}
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    if args.manual:
        cfg = GOLDEN_CASES.get(args.manual)
        if cfg is None:
            print(f"[{args.manual}] unknown bank, skipping")
            return 1
        if not (args.publication_url and args.discovery_file and args.document_file):
            print(f"[{args.manual}] --manual requires --publication-url, --discovery-file, --document-file")
            return 1
        entry = capture_manual(
            args.manual, cfg,
            publication_url=args.publication_url,
            discovery_file=args.discovery_file,
            document_file=args.document_file,
            overwrite=args.overwrite,
        )
        if entry is not None:
            manifest[args.manual] = entry
        MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"manifest: {len(manifest)} golden case(s) at {MANIFEST}")
        return 0

    wanted = args.banks or list(GOLDEN_CASES)
    for bank in wanted:
        if bank not in GOLDEN_CASES:
            print(f"[{bank}] unknown bank, skipping")
            continue
        if not GOLDEN_CASES[bank].get("url_contains"):
            print(f"[{bank}] automated capture not configured (use --manual), skipping")
            continue
        entry = capture(bank, GOLDEN_CASES[bank], overwrite=args.overwrite)
        if entry is not None:
            manifest[bank] = entry

    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"manifest: {len(manifest)} golden case(s) at {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
