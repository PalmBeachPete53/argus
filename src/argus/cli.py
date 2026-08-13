from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from .collector import CentralBankCollector
from .registry import SourceRegistry


def _print_publication(pub) -> None:
    date = pub.publication_date.strftime("%Y-%m-%d") if pub.publication_date else "unknown"
    print(f"{pub.central_bank:>9}  {date}  {pub.status.value:>9}  {pub.title}")
    print(f"           {pub.source_id}  {pub.url}")


def parse_date_bounds(year: int | None, month: str | None) -> tuple[datetime | None, datetime | None]:
    if month:
        parts = month.split("-")
        if len(parts) != 2:
            raise SystemExit(f"Invalid --month {month!r}; use YYYY-MM (e.g. 2026-07)")
        try:
            y, m = int(parts[0]), int(parts[1])
        except ValueError:
            raise SystemExit(f"Invalid --month {month!r}; use YYYY-MM (e.g. 2026-07)")
        if not (1 <= m <= 12):
            raise SystemExit(f"Invalid month in {month!r}; month must be 1..12")
        start = datetime(y, m, 1, tzinfo=timezone.utc)
        next_month = m % 12 + 1
        end_year = y + 1 if m == 12 else y
        end = datetime(end_year, next_month, 1, tzinfo=timezone.utc)
        return start, end
    if year:
        if not (1 <= year <= 9999):
            raise SystemExit(f"Invalid --year {year}")
        return (
            datetime(year, 1, 1, tzinfo=timezone.utc),
            datetime(year + 1, 1, 1, tzinfo=timezone.utc),
        )
    return None, None


def _in_bounds(pub, start, end) -> bool:
    if start is None and end is None:
        return True
    if pub.publication_date is None:
        return False
    if start is not None and pub.publication_date < start:
        return False
    if end is not None and pub.publication_date >= end:
        return False
    return True


def _remove_any(path: Path) -> int:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    return 1


def purge(store_path: str, raw_root: str) -> tuple[int, int]:
    """Delete collected data while preserving the directory tree.

    Removes the SQLite store file, other files directly inside the data
    directory, and all raw documents under ``raw_root``. The ``raw``
    directory itself (and its parents) are kept.
    Returns (files removed under data/, raw entries removed).
    """
    store_path = Path(store_path)
    raw_root = Path(raw_root)

    removed_store = 0
    for suffix in ("", "-wal", "-shm"):
        target = Path(f"{store_path}{suffix}")
        if target.exists():
            target.unlink()
            removed_store += 1

    removed_data = 0
    parent = raw_root.parent
    if parent.exists():
        for child in parent.iterdir():
            if child == raw_root or child == store_path or child.name in (f"{store_path.name}-wal", f"{store_path.name}-shm"):
                continue
            removed_data += _remove_any(child)

    removed_raw = 0
    if raw_root.exists():
        for child in raw_root.iterdir():
            removed_raw += _remove_any(child)

    return removed_store + removed_data, removed_raw


def _run_phase2(args, *, banks, pub_ids) -> int:
    """Normalize stored raw documents and/or classify publications (offline)."""
    from .classification import PublicationClassifier
    from .documents import Normalizer
    from .store import Store

    store = Store(args.store)
    try:
        if args.normalize:
            normalizer = Normalizer(store=store, raw_root=args.raw_root)
            if pub_ids:
                results: list = []
                for pub_id in pub_ids:
                    pub = store.get_publication(pub_id)
                    if pub is None:
                        print(f"  unknown publication: {pub_id}")
                        continue
                    results.extend(normalizer.normalize_publication(pub, force=args.force))
            else:
                results = normalizer.normalize_all(banks=banks, force=args.force)
            bank_of = {}
            for pub in store.list_publications(bank=banks):
                if pub.id:
                    bank_of[pub.id] = pub.central_bank
            by_kind: dict[str, int] = {}
            for doc in results:
                by_kind[doc.document_kind] = by_kind.get(doc.document_kind, 0) + 1
            problems = [d for d in results if d.extraction_warnings]
            by_method: dict[str, int] = {}
            for doc in results:
                by_method[doc.extraction_method] = by_method.get(doc.extraction_method, 0) + 1
            print(f"Normalized {len(results)} document(s) "
                  f"(by format: {(' '.join(f'{k}={v}' for k, v in sorted(by_kind.items()))) or 'none'})")
            print(f"  extraction methods: "
                  f"{' '.join(f'{k}={v}' for k, v in sorted(by_method.items())) or 'none'}")
            if problems:
                print(f"  {len(problems)} document(s) with warnings:")
                for doc in problems:
                    print(f"    {bank_of.get(doc.publication_id, '?')}:{doc.document_kind} "
                          f"[{','.join(doc.extraction_warnings)}] {doc.source_url}")

        if args.classify:
            classifier = PublicationClassifier(store=store)
            if pub_ids:
                classifications = classifier.classify_publications(pub_ids, persist=True)
            else:
                classifications = classifier.classify_all(banks=banks, persist=True)
            print(f"Classified {len(classifications)} publication(s)")
            for c in classifications:
                title = (c.publication_title or "")[:60]
                print(f"  {c.central_bank:>9}  {c.publication_type:<24} "
                      f"{c.confidence.value:>6}  {c.method:<20}  {title}")
        return 0
    finally:
        store.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="argus",
        description="Collect official G10 central bank monetary policy publications.",
    )
    parser.add_argument("--store", default="data/argus.db", help="SQLite store path")
    parser.add_argument("--raw-root", default="data/raw", help="Raw document directory")
    parser.add_argument("--bank", action="append", default=None, help="Restrict to bank id (repeatable)")
    parser.add_argument("--source", action="append", default=None, help="Restrict to source id (repeatable)")
    parser.add_argument("--no-robots", action="store_true", help="Do not honor robots.txt")
    parser.add_argument("--min-interval", type=float, default=1.0, help="Per-host request interval (seconds)")
    parser.add_argument("--list-banks", action="store_true", help="List configured banks and sources")
    parser.add_argument("--discover-only", action="store_true", help="Only discover publications")
    parser.add_argument("--fetch-force", action="store_true", help="Re-fetch already fetched documents")
    parser.add_argument("--year", type=int, default=None, help="Restrict to a publication year (YYYY)")
    parser.add_argument("--month", default=None, help="Restrict to a publication month (YYYY-MM)")
    parser.add_argument("--normalize", action="store_true",
                        help="Normalize already-collected raw documents (no network). "
                             "Reprocesses the raw documents stored locally.")
    parser.add_argument("--classify", action="store_true",
                        help="Classify stored publications (deterministic rules, no network).")
    parser.add_argument("--publication", action="append", default=None,
                        help="Restrict normalization/classification to a publication id (repeatable)")
    parser.add_argument("--force", action="store_true",
                        help="Re-run normalization/classification even if already done")
    parser.add_argument("--purge", action="store_true",
                        help="Delete all collected data (store db and raw documents), keeping the directory structure")
    args = parser.parse_args(argv)

    registry = SourceRegistry()
    if args.list_banks:
        for bank in registry.banks:
            print(f"{bank.id:>9}  {bank.name:<28} {bank.currency}  {bank.official_domain}")
            for source in registry.sources_for_bank(bank.id):
                status = "enabled" if source.enabled else "disabled"
                print(f"          {source.priority:>3}  {status:<8} {source.id:<36} "
                      f"{source.discovery.kind:<8} {source.discovery.url}")
        return 0

    if args.purge:
        removed, raw_entries = purge(args.store, args.raw_root)
        print(f"Purged data: {removed} file(s)/dir(s) under {Path(args.raw_root).parent}, "
              f"{raw_entries} entrie(s) under {args.raw_root}")
        return 0

    banks = tuple(args.bank) if args.bank else None
    pub_ids = tuple(args.publication) if args.publication else None

    if args.normalize or args.classify:
        return _run_phase2(args, banks=banks, pub_ids=pub_ids)

    from .http import HttpConfig

    config = HttpConfig(
        respect_robots=not args.no_robots,
        min_interval=args.min_interval,
    )
    collector = CentralBankCollector(
        store=args.store,
        http_config=config,
        raw_root=args.raw_root,
    )
    banks = tuple(args.bank) if args.bank else None
    source_ids = tuple(args.source) if args.source else None

    date_start, date_end = parse_date_bounds(args.year, args.month)

    if args.discover_only:
        publications = collector.discover_all(banks=banks, source_ids=source_ids)
        publications = [p for p in publications if _in_bounds(p, date_start, date_end)]
        print(f"Discovered {len(publications)} publications")
        for pub in publications:
            _print_publication(pub)
    else:
        result = collector.run(
            banks=banks,
            force=args.fetch_force,
            date_start=date_start,
            date_end=date_end,
        )
        print(f"Run {result.run_id}: {len(result.publications)} publications, "
              f"{len(result.fetch_results)} fetches, {len(result.errors)} errors")
        if result.errors:
            for error in result.errors:
                print(f"  ERROR {error.bank_id}/{error.source_id}: {error.error_type} - {error.message}")
        for pub in result.publications:
            if _in_bounds(pub, date_start, date_end):
                _print_publication(pub)
    return 0


if __name__ == "__main__":
    sys.exit(main())