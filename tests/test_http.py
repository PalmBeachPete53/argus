import pytest

from argus.errors import RobotsDisallowed, TransportError
from argus.http import HttpConfig, HttpClient
from conftest import FakeResponse, FakeSession, response


class _Sleeper:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def make_client(session, **kwargs):
    defaults = dict(respect_robots=False, max_retries=3, backoff_base=1.0, backoff_factor=2.0, jitter=0.0)
    defaults.update(kwargs)
    return HttpClient(HttpConfig(**defaults), session=session, sleeper=kwargs.get("_sleeper") or (lambda _: None))


def test_transport_error_exhausts_retries():
    sleeper = _Sleeper()
    session = FakeSession({"https://x/1": TransportError("https://x/1", "boom")})
    client = HttpClient(
        HttpConfig(respect_robots=False, max_retries=3, backoff_base=1.0, backoff_factor=2.0, jitter=0.0),
        session=session,
        sleeper=sleeper,
    )
    with pytest.raises(TransportError):
        client.get("https://x/1")
    assert len(session.calls) == 4
    assert sleeper.calls == [1.0, 2.0, 4.0]


def test_transport_error_recovers_before_exhaustion():
    sleeper = _Sleeper()
    calls = {"n": 0}

    def flaky(url, headers=None, timeout=None, allow_redirects=True):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransportError(url, "transient")
        return response("<ok/>", url=url, content_type="text/xml")

    session = FakeSession()
    session.get = flaky
    client = HttpClient(
        HttpConfig(respect_robots=False, max_retries=3, jitter=0.0),
        session=session,
        sleeper=sleeper,
    )
    result = client.get("https://x/1")
    assert result.status_code == 200
    assert calls["n"] == 3
    assert sleeper.calls == [1.0, 2.0]


def test_retry_status_codes_then_success():
    calls = {"n": 0}

    def flaky(url, headers=None, timeout=None, allow_redirects=True):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(500, url, b"", {"Content-Type": "text/html"})
        return response("<ok/>", url=url, content_type="text/html")

    session = FakeSession()
    session.get = flaky
    client = make_client(session)
    assert client.get("https://x/1").status_code == 200
    assert calls["n"] == 2


def test_http_error_on_final_status():
    session = FakeSession({"https://x/1": FakeResponse(500, "https://x/1", b"x", {})})
    client = HttpClient(HttpConfig(respect_robots=False, max_retries=0), session=session)
    with pytest.raises(Exception) as exc:
        client.get("https://x/1")
    assert getattr(exc.value, "status_code", None) == 500


def test_rate_limiting_waits_between_requests():
    sleeper = _Sleeper()
    session = FakeSession({
        "https://x/1": response("a", url="https://x/1"),
        "https://x/2": response("b", url="https://x/2"),
    })
    client = HttpClient(
        HttpConfig(respect_robots=False, min_interval=1.0),
        session=session,
        sleeper=sleeper,
    )
    client.get("https://x/1")
    client.get("https://x/2")
    assert sum(sleeper.calls) > 0.9


def test_robots_disallowed_blocks():
    robots_text = "User-agent: *\nDisallow: /forbidden/\n"
    session = FakeSession({
        "https://x.test/robots.txt": response(robots_text, url="https://x.test/robots.txt", content_type="text/plain"),
        "https://x.test/forbidden/a": response("nope"),
    })
    client = HttpClient(
        HttpConfig(respect_robots=True, robots_token="arguscollector"),
        session=session,
    )
    with pytest.raises(RobotsDisallowed):
        client.get("https://x.test/forbidden/a")


def test_robots_allowed_when_not_disallowed():
    robots_text = "User-agent: *\nDisallow: /forbidden/\n"
    session = FakeSession({
        "https://x.test/robots.txt": response(robots_text, url="https://x.test/robots.txt", content_type="text/plain"),
        "https://x.test/ok/a": response("yep"),
    })
    client = HttpClient(
        HttpConfig(respect_robots=True, robots_token="arguscollector"),
        session=session,
    )
    assert client.get("https://x.test/ok/a").status_code == 200