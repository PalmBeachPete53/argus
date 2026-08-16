from __future__ import annotations

from pathlib import Path

import pytest

from argus.errors import TransportError
from argus.http import HttpConfig, HttpClient
from argus.models import CentralBank, DiscoverySpec, Source
from argus.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, status_code, url, content=b"", headers=None):
        self.status_code = status_code
        self.url = url
        self.content = content
        self.headers = headers or {}

    @property
    def content_type(self):
        return self.headers.get("Content-Type")


class FakeSession:
    def __init__(self, routes: dict[str, FakeResponse | Exception] | None = None):
        self.routes = dict(routes or {})
        self.calls: list[str] = []
        self.call_headers: list[dict] = []

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.calls.append(url)
        self.call_headers.append(headers or {})
        route = self.routes.get(url)
        if route is None:
            raise TransportError(url, "no route registered")
        if isinstance(route, Exception):
            raise route
        return route


def response(content: str | bytes, *, status=200, url="https://example.org/page", content_type="text/html"):
    body = content.encode("utf-8") if isinstance(content, str) else content
    return FakeResponse(status, url, body, {"Content-Type": content_type})


def make_client(session: FakeSession, *, respect_robots=False):
    config = HttpConfig(respect_robots=respect_robots, min_interval=0.0)
    return HttpClient(config, session=session, sleeper=lambda _: None)


def make_store(tmp_path) -> Store:
    return Store(tmp_path / "argus.db")


def make_source(*, id_="src", bank="x", name="Source", kind="rss", url="https://x.example/feed.xml", **spec_kwargs):
    return Source(
        id=id_,
        central_bank=bank,
        name=name,
        discovery=DiscoverySpec(kind=kind, url=url, **spec_kwargs),
    )


@pytest.fixture
def fixture_bytes():
    def load(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return load


@pytest.fixture
def fixture_text(fixture_bytes):
    def load(name: str) -> str:
        return fixture_bytes(name).decode("utf-8")

    return load


def BANK(id_="bank", name="Bank", currency="XXX", domain="example.org") -> CentralBank:
    return CentralBank(id=id_, name=name, currency=currency, official_domain=domain)


@pytest.fixture(autouse=True)
def _isolate_bank_overrides(tmp_path, monkeypatch):
    """Point the persistent bank-override file at a per-test temp path so the
    suite is hermetic: a bank toggle written by a developer's desktop GUI (in
    the real ``data/`` directory) never leaks into the test run, and test
    writes never touch the real configuration."""
    monkeypatch.setenv("ARGUS_BANKS_CONFIG", str(tmp_path / "banks.json"))
    yield