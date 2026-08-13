from argus.cli import purge


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
