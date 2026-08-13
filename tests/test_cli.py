from __future__ import annotations

from argus.cli import main, purge
from argus.models import Document, DocumentStatus, Publication


def _seed_store(store, *, bank="ecb"):
    pub = store.upsert_publication(
        Publication(
            central_bank=bank,
            title="Monetary policy decisions",
            url=f"https://www.{bank}.test/a",
            source_id="src",
            source_url="https://www.example.org/feed",
        )
    )
    doc = store.upsert_document(
        Document(
            publication_id=pub.id,
            url="https://www.example.org/raw.pdf",
            kind="pdf",
            status=DocumentStatus.FETCHED,
            sha256="s" * 64,
        )
    )
    return pub, doc


def test_purge_removes_data_keeps_dirs(tmp_path):
    store = tmp_path / "data" / "argus.db"
    raw = tmp_path / "data" / "raw"
    (raw / "ecb" / "2026" / "07").mkdir(parents=True)
    (raw / "ecb" / "2026" / "07" / "doc.pdf").write_text("x")
    (raw / "fed" / "2026").mkdir(parents=True)
    (raw / "fed" / "2026" / "stmt.html").write_text("y")
    store.write_text("sqlite")

    removed, raw_entries = purge(str(store), str(raw))

    assert removed >= 1  # the db file
    assert raw_entries == 2  # ecb/, fed/ top-level entries
    assert not store.exists()
    assert raw.exists()  # raw dir itself kept
    assert list(raw.iterdir()) == []  # empty
    assert not (raw / "ecb").exists()


def test_purge_idempotent_and_missing_ok(tmp_path):
    store = tmp_path / "nope.db"
    raw = tmp_path / "raw"
    removed, raw_entries = purge(str(store), str(raw))
    assert removed == 0 and raw_entries == 0
    removed, raw_entries = purge(str(store), str(raw))
    assert removed == 0 and raw_entries == 0


def test_purge_keeps_store_in_data_dir_between_raw_and_parent(tmp_path):
    data = tmp_path / "data"
    raw = data / "raw"
    raw.mkdir(parents=True)
    (raw / "x.txt").write_text("x")
    store = data / "argus.db"
    store.write_text("sqlite")
    (data / "stray.log").write_text("log")

    removed, raw_entries = purge(str(store), str(raw))

    assert removed == 2  # argus.db + stray.log
    assert raw_entries == 1
    assert data.exists()
    assert raw.exists()
    assert not store.exists()
    assert not (data / "stray.log").exists()


def test_report_summarizes_store(capsys, tmp_path):
    from conftest import make_store
    from argus.documents import NormalizedDocument

    store = make_store(tmp_path)
    pub, _ = _seed_store(store)
    store.upsert_normalized_document(
        NormalizedDocument(
            publication_id=pub.id,
            document_id="d" * 64,
            source_url="https://www.example.org/raw.pdf",
            local_path=None,
            document_kind="pdf",
            extraction_method="pdf_text",
            text="",
        )
    )
    store.set_classification(
        pub.id,
        central_bank="ecb",
        publication_type="monetary_policy_decision",
        confidence="high",
        method="source_type_hint",
        evidence=["type_hint=monetary_policy_decision"],
    )

    code = main(["--report", "--store", str(tmp_path / "argus.db"), "--bank", "ecb"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Store report:" in out
    assert "publications" in out and "ecb=1" in out
    assert "normalized docs" in out
    assert "classifications" in out
    assert "monetary_policy_decision=1" in out
    assert "high=1" in out
