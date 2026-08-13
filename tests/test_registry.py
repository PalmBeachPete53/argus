from argus.registry import SourceRegistry


def test_all_ten_g10_banks_present():
    registry = SourceRegistry()
    bank_ids = {bank.id for bank in registry.banks}
    expected = {"fed", "ecb", "boe", "boj", "snb", "boc", "rba", "rbnz", "norges", "riksbank"}
    assert expected <= bank_ids


def test_bank_lookup():
    registry = SourceRegistry()
    assert registry.bank("fed").currency == "USD"
    assert registry.bank("rba").official_domain == "rba.gov.au"
    assert registry.bank("missing") is None


def test_every_bank_has_sources():
    registry = SourceRegistry()
    for bank in registry.banks:
        sources = registry.sources_for_bank(bank.id)
        assert sources, f"{bank.id} has no sources"
        assert all(s.central_bank == bank.id for s in sources)


def test_sources_sorted_by_priority():
    registry = SourceRegistry()
    for bank in registry.banks:
        sources = registry.sources_for_bank(bank.id)
        priorities = [s.priority for s in sources]
        assert priorities == sorted(priorities)


def test_sources_have_known_discovery_kinds():
    registry = SourceRegistry()
    for source in registry.sources:
        assert source.discovery.kind in ("rss", "sitemap", "html")
        assert source.discovery.url.startswith("https://")


def test_enabled_sources_filter():
    registry = SourceRegistry()
    fed = registry.enabled_sources(banks=["fed"])
    assert fed and all(s.central_bank == "fed" for s in fed)
    by_ids = registry.enabled_sources(source_ids=["fed_monetary_press_rss"])
    assert len(by_ids) == 1


def test_source_lookup():
    registry = SourceRegistry()
    source = registry.source("rba_media_releases_rss")
    assert source is not None
    assert source.central_bank == "rba"