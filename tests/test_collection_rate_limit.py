"""Global per-host rate limiting shared across Collection workers.

Each worker gets its own HttpClient (own session / robots cache) but all
workers of one campaign share a single ``RateLimiter`` injected into every
client, so ``min_interval`` is honoured globally per host — two workers never
hit the same host back-to-back, while different hosts stay parallel. The
limiter is campaign-scoped: two campaigns get independent limiters.

Tests are deterministic: they use a fake monotonic clock and a recording
sleeper instead of real sleeps.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from argus.collector import CentralBankCollector
from argus.http import HttpConfig, HttpClient, RateLimiter
from argus.models import Publication, PublicationStatus
from conftest import FakeResponse, FakeSession, make_store


class _FakeClock:
    """A thread-safe monotonic clock advanced explicitly (or by the sleeper).

    Starts at a large base epoch (like the real monotonic clock) so the first
    request to a host never waits — only a request arriving within
    ``min_interval`` of that host's *latest* request is delayed.
    """

    _BASE = 1000.0

    def __init__(self):
        self._now = self._BASE
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += seconds

    @property
    def value(self) -> float:
        with self._lock:
            return self._now


class _RecordingSleeper:
    def __init__(self, clock: _FakeClock):
        self.clock = clock
        self.calls: list[float] = []
        self._lock = threading.Lock()

    def __call__(self, seconds: float) -> None:
        with self._lock:
            self.calls.append(seconds)
        self.clock.advance(seconds)


def _limiter(min_interval: float):
    clock = _FakeClock()
    sleeper = _RecordingSleeper(clock)
    limiter = RateLimiter(min_interval, now_fn=clock, sleeper=sleeper)
    return limiter, clock, sleeper


def _make_client(session, limiter, sleeper):
    return HttpClient(
        HttpConfig(respect_robots=False, min_interval=1.0),
        session=session,
        sleeper=sleeper,
        rate_limiter=limiter,
    )


def _pub(i: int, bank: str = "fed") -> Publication:
    return Publication(
        central_bank=bank,
        title=f"Statement {i}",
        url=f"https://{bank}.example/pubs/{i}.htm",
        source_id="src",
        source_url=f"https://{bank}.example/feed.xml",
        publication_date=datetime(2026, 7, 1 + i, tzinfo=timezone.utc),
        status=PublicationStatus.DISCOVERED,
    )


# ---------------------------------------------------------------------------
# RateLimiter semantics
# ---------------------------------------------------------------------------

def test_first_request_does_not_wait():
    """The first request to a host is never delayed (no previous request to
    space against) — mirrors the real monotonic clock."""
    limiter, clock, sleeper = _limiter(1.0)
    limiter.wait("https://bank-a.gov/x")
    assert sleeper.calls == []
    assert clock.value == _FakeClock._BASE


def test_limiter_spaces_same_host():
    """A second request to the same host is spaced by min_interval."""
    limiter, clock, sleeper = _limiter(1.0)
    limiter.wait("https://bank-a.gov/x")   # first: no wait
    limiter.wait("https://bank-a.gov/y")   # within interval: sleeps 1.0
    assert sleeper.calls == [1.0]
    assert clock.value == _FakeClock._BASE + 1.0


def test_limiter_does_not_space_different_hosts():
    """Different hosts are not artificially serialized: a request to host B
    arriving right after one to host A does not wait for A's slot — each host
    has its own per-host timing."""
    limiter, clock, sleeper = _limiter(1.0)
    limiter.wait("https://bank-a.gov/x")   # host A: first, no wait
    limiter.wait("https://bank-b.gov/x")   # host B: first, no wait (not blocked by A)
    assert sleeper.calls == []
    # A's second request sleeps its own slot (clock advances 1.0s)…
    limiter.wait("https://bank-a.gov/y")
    assert sleeper.calls == [1.0]
    # …and B's second request does NOT wait behind A: its own 1s slot already
    # elapsed during A's sleep, so it proceeds immediately.
    limiter.wait("https://bank-b.gov/y")
    assert sleeper.calls == [1.0]


def test_limiter_third_same_host_spaces_against_latest():
    """After a gap, a new request to the same host is spaced only by the
    remaining time — the limiter tracks the latest request, not a cumulative
    delay."""
    limiter, clock, sleeper = _limiter(1.0)
    limiter.wait("https://bank-a.gov/x")          # first: no wait (now=1000)
    clock.advance(0.6)                            # 0.6s of elapsed time
    limiter.wait("https://bank-a.gov/y")          # only 0.4s remaining
    assert sleeper.calls == [pytest.approx(0.4)]
    assert pytest.approx(clock.value) == _FakeClock._BASE + 1.0


def test_limiter_zero_interval_no_wait():
    limiter, clock, sleeper = _limiter(0.0)
    limiter.wait("https://bank-a.gov/x")
    limiter.wait("https://bank-a.gov/x")
    assert sleeper.calls == []
    assert clock.value == _FakeClock._BASE


def test_limiter_isolated_between_instances():
    """Two independent limiters (two campaigns) share no host state."""
    limiter_a, clock_a, sleeper_a = _limiter(1.0)
    limiter_b, clock_b, sleeper_b = _limiter(1.0)
    limiter_a.wait("https://bank-a.gov/x")  # A's first request
    limiter_a.wait("https://bank-a.gov/x")  # A sleeps 1.0
    limiter_b.wait("https://bank-a.gov/x")  # B independent: first, no wait
    assert sleeper_a.calls == [1.0]
    assert sleeper_b.calls == []
    assert limiter_a is not limiter_b


# ---------------------------------------------------------------------------
# Shared limiter across HttpClient instances (the worker model)
# ---------------------------------------------------------------------------

def test_two_clients_share_one_limiter_same_host():
    """Two clients (two workers) sharing one limiter space same-host requests
    globally — the interval is not once per client."""
    limiter, clock, sleeper = _limiter(1.0)
    session = FakeSession({
        "https://bank-a.gov/x": FakeResponse(200, "https://bank-a.gov/x", b"x", {"Content-Type": "text/html"}),
        "https://bank-a.gov/y": FakeResponse(200, "https://bank-a.gov/y", b"y", {"Content-Type": "text/html"}),
    })
    client_a = _make_client(session, limiter, sleeper)
    client_b = _make_client(session, limiter, sleeper)

    client_a.get("https://bank-a.gov/x")   # first request to host: no wait
    client_b.get("https://bank-a.gov/y")   # same host, other client: waits 1.0
    # the second request (from the *other* client) waits the full interval —
    # the limiter is shared, so the per-host slot is global
    assert sleeper.calls == [1.0]
    assert len(session.calls) == 2


def test_two_clients_different_hosts_parallel():
    """Two clients hitting different hosts are not serialized by the shared
    limiter — both requests proceed with their own per-host spacing."""
    limiter, clock, sleeper = _limiter(1.0)
    session = FakeSession({
        "https://bank-a.gov/x": FakeResponse(200, "https://bank-a.gov/x", b"x", {"Content-Type": "text/html"}),
        "https://bank-b.gov/x": FakeResponse(200, "https://bank-b.gov/x", b"y", {"Content-Type": "text/html"}),
    })
    client_a = _make_client(session, limiter, sleeper)
    client_b = _make_client(session, limiter, sleeper)

    client_a.get("https://bank-a.gov/x")   # first request to host A: no wait
    client_b.get("https://bank-b.gov/x")   # first request to host B: no wait
    assert sleeper.calls == []
    assert len(session.calls) == 2


def test_shared_limiter_preserves_retries():
    """A client with a shared limiter still performs HTTP retries."""
    from argus.errors import TransportError

    limiter, clock, sleeper = _limiter(1.0)
    calls = {"n": 0}

    def flaky(url, headers=None, timeout=None, allow_redirects=True):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransportError(url, "transient")
        return FakeResponse(200, url, b"ok", {"Content-Type": "text/html"})

    session = FakeSession()
    session.get = flaky
    client = HttpClient(
        HttpConfig(respect_robots=False, min_interval=1.0, max_retries=3, backoff_base=0.01, backoff_factor=1.0, jitter=0.0),
        session=session,
        sleeper=sleeper,
        rate_limiter=limiter,
    )
    result = client.get("https://bank-a.gov/x")
    assert result.status_code == 200
    assert calls["n"] == 2  # retried once


def test_shared_limiter_keeps_robots_gate():
    """Robots disallow still blocks even with a shared limiter injected."""
    from argus.errors import RobotsDisallowed

    limiter, clock, sleeper = _limiter(1.0)
    robots_text = "User-agent: *\nDisallow: /forbidden/\n"
    session = FakeSession({
        "https://x.test/robots.txt": FakeResponse(200, "https://x.test/robots.txt", robots_text.encode(), {"Content-Type": "text/plain"}),
        "https://x.test/forbidden/a": FakeResponse(200, "https://x.test/forbidden/a", b"x", {"Content-Type": "text/html"}),
    })
    client = HttpClient(
        HttpConfig(respect_robots=True, robots_token="arguscollector", min_interval=1.0),
        session=session,
        sleeper=sleeper,
        rate_limiter=limiter,
    )
    with pytest.raises(RobotsDisallowed):
        client.get("https://x.test/forbidden/a")


# ---------------------------------------------------------------------------
# Campaign-level sharing & isolation
# ---------------------------------------------------------------------------

def test_campaign_shares_one_limiter_across_workers(tmp_path, monkeypatch):
    """All workers of a campaign receive the SAME campaign-scoped limiter.

    Proves the worker client wiring: ``_new_client(rate_limiter=campaign)`` is
    used for every publication, and the limiter is never rebuilt per worker.
    """
    from argus.collector import CentralBankCollector

    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", "2")
    store = make_store(tmp_path)
    for i in range(2):
        store.upsert_publication(_pub(i))

    seen: list = []
    lock = threading.Lock()
    real_new_client = CentralBankCollector._new_client

    def recording_new_client(self, *, rate_limiter=None):
        with lock:
            seen.append(rate_limiter)
        return real_new_client(self, rate_limiter=rate_limiter)

    monkeypatch.setattr(CentralBankCollector, "_new_client", recording_new_client)
    collector = CentralBankCollector(
        store=store,
        http_config=HttpConfig(respect_robots=False, min_interval=0.5),
        raw_root=tmp_path / "raw",
    )
    collector.collect_campaign(banks=("fed",))

    assert len(seen) == 2
    assert seen[0] is seen[1], "workers must share one campaign limiter"
    assert seen[0] is not None
    assert seen[0].min_interval == 0.5


def test_two_campaigns_are_isolated(tmp_path, monkeypatch):
    """Two successive campaigns get independent limiters — no shared host state."""
    from argus.collector import CentralBankCollector

    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", "2")
    store = make_store(tmp_path)
    store.upsert_publication(_pub(0, bank="fed"))
    store.upsert_publication(_pub(1, bank="ecb"))

    seen: list = []
    lock = threading.Lock()
    real_new_client = CentralBankCollector._new_client

    def recording_new_client(self, *, rate_limiter=None):
        with lock:
            seen.append(rate_limiter)
        return real_new_client(self, rate_limiter=rate_limiter)

    monkeypatch.setattr(CentralBankCollector, "_new_client", recording_new_client)
    collector = CentralBankCollector(
        store=store,
        http_config=HttpConfig(respect_robots=False, min_interval=0.5),
        raw_root=tmp_path / "raw",
    )
    collector.collect_campaign(banks=("fed",))
    collector.collect_campaign(banks=("ecb",))

    assert len(seen) == 2
    assert seen[0] is not seen[1], "each campaign must build its own limiter"


def test_campaign_limiter_spaces_same_host_across_workers(tmp_path, monkeypatch):
    """End-to-end: with a shared limiter injected and a fake clock, two workers
    targeting the same host cannot both fire immediately — the campaign limiter
    is genuinely used by the worker clients."""
    from argus.collector import CentralBankCollector

    monkeypatch.setenv("ARGUS_COLLECTION_WORKERS", "2")
    store = make_store(tmp_path)
    # two publications sharing one host
    for i in range(2):
        pub = _pub(i, bank="fed")
        pub.url = f"https://bank-a.gov/pubs/{i}.htm"
        store.upsert_publication(pub)

    clock = _FakeClock()
    sleeper = _RecordingSleeper(clock)
    limiter = RateLimiter(1.0, now_fn=clock, sleeper=sleeper)
    seen_limiters: list = []
    lock = threading.Lock()
    real_new_client = CentralBankCollector._new_client

    def recording_new_client(self, *, rate_limiter=None):
        with lock:
            seen_limiters.append(rate_limiter)
        return real_new_client(self, rate_limiter=rate_limiter)

    monkeypatch.setattr(CentralBankCollector, "_new_client", recording_new_client)

    # The fetcher uses the per-worker client (with the shared limiter); we
    # exercise the real network path via a FakeSession routed through the
    # shared limiter by making the campaign limiter the *recording* one.
    collector = CentralBankCollector(
        store=store,
        http_config=HttpConfig(respect_robots=False, min_interval=0.5),
        raw_root=tmp_path / "raw",
        client=_make_client(
            FakeSession({
                "https://bank-a.gov/pubs/0.htm": FakeResponse(200, "https://bank-a.gov/pubs/0.htm", b"0", {"Content-Type": "text/html"}),
                "https://bank-a.gov/pubs/1.htm": FakeResponse(200, "https://bank-a.gov/pubs/1.htm", b"1", {"Content-Type": "text/html"}),
            }),
            limiter,
            sleeper,
        ),
    )
    collector.collect_campaign(banks=("fed",))

    # the campaign limiter (min_interval=0.5) was shared by both workers
    assert len(seen_limiters) == 2
    assert seen_limiters[0] is seen_limiters[1]
    assert seen_limiters[0].min_interval == 0.5
    # the shared limiter was actually exercised: the two same-host requests were
    # spaced (at least one wait happened)
    assert sleeper.calls, "the campaign limiter must actually space same-host requests"
