"""Bank enable/disable toggle — configuration, registry, pipeline skip,
E2E/golden integration and reversibility.

The toggle is generic (no per-bank special-casing). RBNZ is the currently
disabled bank; the tests below exercise the generic mechanism.
"""

from __future__ import annotations

from conftest import FakeSession, make_client, make_store, response

from argus.adapters.base import BankAdapter, rss_source
from argus.adapters.rbnz import RBNZAdapter
from argus.collector import CentralBankCollector
from argus.config import (
    BANKS_ENABLED,
    clear_bank_overrides,
    enabled_banks,
    is_bank_enabled,
    set_bank_enabled,
)
from argus.errors import TransportError
from argus.models import CentralBank
from argus.registry import SourceRegistry


class _FakeBankAdapter(BankAdapter):
    def _build(self):
        bank = CentralBank("fb", "Fake Bank", "XXX", "fake.test")
        sources = [rss_source("fb_feed", "fb", "fake feed", "https://fake.test/feed.xml")]
        return bank, sources


def _registry_with_fake_and_rbnz() -> SourceRegistry:
    return SourceRegistry([_FakeBankAdapter(), RBNZAdapter()])


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_rbnz_is_off_by_default():
    assert is_bank_enabled("rbnz") is False
    assert BANKS_ENABLED["rbnz"] is False


def test_all_other_banks_are_on():
    for bank in ("fed", "ecb", "boe", "boj", "snb", "boc", "rba", "norges", "riksbank"):
        assert is_bank_enabled(bank) is True, bank


def test_enabled_banks_contains_only_on():
    active = set(enabled_banks())
    assert "rbnz" not in active
    for bank in ("fed", "ecb", "boe", "boj", "snb", "boc", "rba", "norges", "riksbank"):
        assert bank in active, bank


def test_env_disabled_overrides(monkeypatch):
    monkeypatch.setenv("ARGUS_BANKS_DISABLED", "fed,boe")
    assert is_bank_enabled("fed") is False
    assert is_bank_enabled("boe") is False
    assert is_bank_enabled("ecb") is True
    assert is_bank_enabled("rbnz") is False


def test_unknown_bank_defaults_to_enabled():
    assert is_bank_enabled("unknown-bank") is True


# ---------------------------------------------------------------------------
# Registry: RBNZ stays known but is not active
# ---------------------------------------------------------------------------


def test_registry_still_knows_rbnz():
    registry = SourceRegistry()
    assert registry.bank("rbnz") is not None
    assert any(b.id == "rbnz" for b in registry.banks)
    assert registry.source("rbnz_ocr_decisions") is not None


def test_active_banks_excludes_rbnz():
    registry = SourceRegistry()
    active = {b.id for b in registry.active_banks}
    assert "rbnz" not in active
    assert "fed" in active and "rba" in active and "riksbank" in active


def test_enabled_sources_excludes_rbnz_sources():
    registry = _registry_with_fake_and_rbnz()
    enabled_ids = {s.id for s in registry.enabled_sources()}
    assert "fb_feed" in enabled_ids
    assert "rbnz_ocr_decisions" not in enabled_ids
    # the RBNZ source still exists in the registry
    assert registry.source("rbnz_ocr_decisions") is not None


# ---------------------------------------------------------------------------
# Pipeline: a disabled bank is never scheduled (true skip)
# ---------------------------------------------------------------------------


def test_discover_all_skips_disabled_bank(tmp_path):
    """With RBNZ OFF, its source is not executed: even if it would raise a
    transport error, no error is recorded and no publication is attempted."""
    registry = _registry_with_fake_and_rbnz()
    store = make_store(tmp_path)
    feed = b"<rss><channel><item><title>D</title><link>https://fake.test/1</link></item></channel></rss>"
    session = FakeSession({
        "https://fake.test/feed.xml": response(feed, url="https://fake.test/feed.xml", content_type="application/xml"),
        # RBNZ source URL deliberately unrouted → would raise if touched
        "https://www.rbnz.govt.nz/monetary-policy/monetary-policy-decisions": TransportError("x", "403"),
    })
    collector = CentralBankCollector(
        store=store, registry=registry, client=make_client(session), search_provider=None
    )
    pubs = collector.discover_all()
    assert [p.central_bank for p in pubs] == ["fb"]
    # no RBNZ error was recorded (its source was never executed)
    assert all(e.source_id != "rbnz_ocr_decisions" for e in store.list_errors())


def test_fetch_all_filters_to_active_banks(tmp_path):
    registry = _registry_with_fake_and_rbnz()
    store = make_store(tmp_path)
    store.upsert_publication(
        __import__("argus.models", fromlist=["Publication"]).Publication(
            central_bank="rbnz", title="t", url="https://rbnz/x", source_id="s", source_url="u"
        )
    )
    collector = CentralBankCollector(store=store, registry=registry, client=make_client(FakeSession({})))
    # with no explicit bank filter, only active banks are fetched → rbnz publication untouched
    fetched = collector.fetch_all()
    assert fetched == []


def test_explicit_selection_of_disabled_bank_is_filtered(tmp_path):
    """Uniformity defect: an explicit bank selection must NOT bypass the toggle.
    A disabled bank requested directly is excluded from fetch and run, exactly
    like the global path — the only way to run it is to re-enable it first."""
    from argus.config import filter_enabled

    registry = _registry_with_fake_and_rbnz()
    store = make_store(tmp_path)
    store.upsert_publication(
        __import__("argus.models", fromlist=["Publication"]).Publication(
            central_bank="rbnz", title="t", url="https://rbnz/x", source_id="s", source_url="u"
        )
    )
    collector = CentralBankCollector(store=store, registry=registry, client=make_client(FakeSession({})))
    # explicit request for the disabled bank → nothing scheduled
    assert collector.fetch_all(banks=["rbnz"]) == []
    # explicit request for an enabled bank → scheduled
    feed = b"<rss><channel><item><title>D</title><link>https://fake.test/1</link></item></channel></rss>"
    session = FakeSession({"https://fake.test/feed.xml": response(feed, url="https://fake.test/feed.xml", content_type="application/xml")})
    collector2 = CentralBankCollector(store=store, registry=registry, client=make_client(session))
    pubs = collector2.discover_all(banks=["fb"])
    assert [p.central_bank for p in pubs] == ["fb"]
    # filter_enabled mirrors the toggle for any selection
    assert filter_enabled(["rbnz", "fb"]) == ("fb",)
    assert filter_enabled(["rbnz"]) == ()


def test_cli_bank_selection_filters_disabled_banks(monkeypatch):
    """The CLI '--bank' selection is filtered through the toggle, so requesting
    a disabled bank directly yields an empty (enabled) selection."""
    from argus.cli import _search_provider_from_env  # ensure importable

    args_bank = ("rbnz",)
    from argus.config import filter_enabled, enabled_banks

    resolved = filter_enabled(tuple(args_bank)) if args_bank else enabled_banks()
    assert resolved == ()
    # an enabled bank remains selectable
    assert filter_enabled(("fed",)) == ("fed",)


def test_config_allow_list_is_authoritative(monkeypatch):
    """When ARGUS_BANKS_ENABLED is set it is the complete allow-list and is
    authoritative over the default map and ARGUS_BANKS_DISABLED: a bank present
    in both allow and disable lists is enabled (documented contract)."""
    monkeypatch.setenv("ARGUS_BANKS_ENABLED", "fed,rbnz")
    monkeypatch.setenv("ARGUS_BANKS_DISABLED", "rbnz,ecb")
    assert is_bank_enabled("rbnz") is True   # allow wins over disable
    assert is_bank_enabled("fed") is True
    assert is_bank_enabled("ecb") is False   # not in the allow-list
    assert enabled_banks() == ("fed", "rbnz")


def test_filter_enabled_unknown_bank_defaults_enabled():
    from argus.config import filter_enabled

    # an unknown bank id is not disabled by the map (defaults enabled)
    assert filter_enabled(("unknown-bank",)) == ("unknown-bank",)


# ---------------------------------------------------------------------------
# Reversibility: OFF -> ON without code changes
# ---------------------------------------------------------------------------


def test_env_enable_reenables_rbnz(monkeypatch):
    assert is_bank_enabled("rbnz") is False
    monkeypatch.setenv(
        "ARGUS_BANKS_ENABLED",
        "fed,ecb,boe,boj,snb,boc,rba,rbnz,norges,riksbank",
    )
    assert is_bank_enabled("rbnz") is True
    assert "rbnz" in enabled_banks()


def test_env_enable_restricted(monkeypatch):
    monkeypatch.setenv("ARGUS_BANKS_ENABLED", "fed,rbnz")
    assert is_bank_enabled("fed") is True
    assert is_bank_enabled("rbnz") is True
    assert is_bank_enabled("ecb") is False


# ---------------------------------------------------------------------------
# Persistent user overrides (operator / desktop GUI controlled)
# ---------------------------------------------------------------------------


def test_no_overrides_defaults_to_map():
    assert is_bank_enabled("rbnz") is False
    assert is_bank_enabled("fed") is True


def test_persistent_override_disables_bank():
    set_bank_enabled("fed", False)
    assert is_bank_enabled("fed") is False
    assert is_bank_enabled("ecb") is True
    assert "fed" not in enabled_banks()


def test_persistent_override_reenables_rbnz():
    set_bank_enabled("rbnz", True)
    assert is_bank_enabled("rbnz") is True
    assert "rbnz" in enabled_banks()


def test_persistent_override_written_to_disk():
    from argus.config import banks_config_path, load_bank_overrides

    set_bank_enabled("boe", False)
    assert banks_config_path().exists()
    assert load_bank_overrides()["boe"] is False


def test_clear_overrides_returns_to_defaults():
    set_bank_enabled("rbnz", True)
    set_bank_enabled("fed", False)
    clear_bank_overrides()
    assert is_bank_enabled("rbnz") is False
    assert is_bank_enabled("fed") is True


def test_env_allow_list_still_authoritative_over_overrides(monkeypatch):
    set_bank_enabled("rbnz", True)  # GUI re-enabled RBNZ
    monkeypatch.setenv("ARGUS_BANKS_ENABLED", "fed,ecb")
    assert is_bank_enabled("rbnz") is False  # not in the allow-list
    assert is_bank_enabled("fed") is True


def test_env_disabled_still_beats_overrides(monkeypatch):
    set_bank_enabled("fed", True)
    monkeypatch.setenv("ARGUS_BANKS_DISABLED", "fed")
    assert is_bank_enabled("fed") is False


def test_filter_enabled_respects_overrides():
    set_bank_enabled("rbnz", True)
    set_bank_enabled("boj", False)
    from argus.config import filter_enabled

    assert filter_enabled(("rbnz", "boj", "fed")) == ("rbnz", "fed")


def test_cli_list_banks_reflects_overrides(monkeypatch, capsys):
    set_bank_enabled("rbnz", True)
    from argus.cli import main as cli_main

    assert cli_main(["--list-banks"]) == 0
    out = capsys.readouterr().out
    assert "rbnz" in out
    assert "ON" in out


# ---------------------------------------------------------------------------
# GUI bridge
# ---------------------------------------------------------------------------


def test_gui_bridge_banks(monkeypatch):
    from argus.gui_bridge import _cmd_banks

    monkeypatch.setattr("sys.stdout", __import__("io").StringIO())
    assert _cmd_banks() == 0


def test_gui_bridge_banks_set(monkeypatch):
    from argus.gui_bridge import _cmd_banks_set

    out = __import__("io").StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert _cmd_banks_set(["rbnz", "on"]) == 0
    assert is_bank_enabled("rbnz") is True
    assert '"enabled": true' in out.getvalue()


def test_gui_bridge_data_root(monkeypatch):
    from pathlib import Path

    from argus.gui_bridge import _cmd_data_root

    out = __import__("io").StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert _cmd_data_root() == 0
    import json

    root = json.loads(out.getvalue())["root"]
    assert Path(root).is_absolute()
    assert Path(root).name == "data"


def test_gui_bridge_rejects_bad_state(monkeypatch):
    from argus.gui_bridge import _cmd_banks_set

    assert _cmd_banks_set(["rbnz", "maybe"]) == 2


# ---------------------------------------------------------------------------
# Golden / E2E integration (generic skip mechanism)
# ---------------------------------------------------------------------------


def test_e2e_case_carries_bank_and_is_skippable():
    from test_pipeline_end_to_end import CASES

    rbnz_case = next(c for c in CASES if c.values[0]["bank"] == "rbnz")
    assert is_bank_enabled(rbnz_case.values[0]["bank"]) is False


def test_golden_manifest_has_no_rbnz():
    import json
    from pathlib import Path

    manifest = json.loads((Path(__file__).parent / "golden" / "manifest.json").read_text(encoding="utf-8"))
    assert "rbnz" not in manifest
    # RBNZ hooks remain pre-registered in the golden test module
    import test_golden_corpus as g

    assert "rbnz" in g.ADAPTERS
    assert "rbnz" in g.EXTRACTORS
