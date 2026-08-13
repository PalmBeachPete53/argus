from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from email.message import Message
from urllib.parse import urlparse

from .errors import HttpError, RobotsDisallowed, TransportError
from .robots import RobotsGate

DEFAULT_USER_AGENT = (
    "ArgusCollector/0.1 (official central bank publication collection; "
    "contact: argus@example.invalid)"
)

DEFAULT_RETRY_STATUSES = (408, 429, 500, 502, 503, 504)


@dataclass(frozen=True)
class HttpConfig:
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = 25.0
    max_retries: int = 3
    backoff_base: float = 1.0
    backoff_factor: float = 2.0
    backoff_max: float = 30.0
    jitter: float = 0.2
    min_interval: float = 1.0
    respect_robots: bool = True
    robots_token: str = "arguscollector"
    robots_on_error: str = "allow"
    robots_ttl_seconds: float = 86400.0
    retry_statuses: tuple[int, ...] = DEFAULT_RETRY_STATUSES


@dataclass
class HttpResponse:
    status_code: int
    url: str
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str | None = None
    from_cache: bool = False
    elapsed: float = 0.0

    @property
    def text(self) -> str:
        if not self.content:
            return ""
        ctype = (self.content_type or "").lower()
        encoding = "utf-8"
        if ctype:
            message = Message()
            message["Content-Type"] = ctype
            charset = message.get_content_charset()
            if charset:
                encoding = charset
        try:
            return self.content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            return self.content.decode("utf-8", errors="replace")


class RateLimiter:
    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str, sleeper=time.sleep) -> None:
        if not self.min_interval or self.min_interval <= 0:
            return
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return
        with self._lock:
            last = self._last.get(host, 0.0)
            now = time.monotonic()
            delay = last + self.min_interval - now
            if delay > 0:
                self._last[host] = now + delay
            else:
                self._last[host] = now
                delay = 0.0
        if delay > 0:
            sleeper(delay)


class HttpClient:
    def __init__(
        self,
        config: HttpConfig | None = None,
        *,
        session=None,
        sleeper=None,
    ) -> None:
        self.config = config or HttpConfig()
        self._session = session
        self.sleeper = sleeper or time.sleep
        self.rate_limiter = RateLimiter(self.config.min_interval)
        self.robots = RobotsGate(
            self._raw_get,
            token=self.config.robots_token,
            ttl_seconds=self.config.robots_ttl_seconds,
            on_error=self.config.robots_on_error,
        )
        self._last_request: dict[str, float] = {}
        self._request_count = 0
        self._host_lock = threading.Lock()

    def _real_session(self):
        if self._session is None:
            import requests

            session = requests.Session()
            self._session = session
        return self._session

    def _raw_get(self, url: str, *, headers=None) -> HttpResponse:
        session = self._real_session()
        request_headers = {"User-Agent": self.config.user_agent}
        if headers:
            request_headers.update(headers)
        try:
            response = session.get(
                url,
                headers=request_headers,
                timeout=self.config.timeout,
                allow_redirects=True,
            )
        except Exception as exc:  # requests.Timeout, ConnectionError, etc.
            raise TransportError(url, exc.__class__.__name__) from exc
        return HttpResponse(
            status_code=response.status_code,
            url=getattr(response, "url", None) or url,
            content=response.content or b"",
            headers=dict(response.headers or {}),
            content_type=response.headers.get("Content-Type"),
        )

    def _backoff(self, attempt: int) -> float:
        delay = min(
            self.config.backoff_max,
            self.config.backoff_base * (self.config.backoff_factor ** (attempt - 1)),
        )
        jitter = self.config.jitter
        if jitter:
            delay = delay * (1.0 + random.uniform(-jitter, jitter))
        return delay

    def get(
        self,
        url: str,
        *,
        headers: dict | None = None,
        respect_robots: bool | None = None,
    ) -> HttpResponse:
        should_respect = self.config.respect_robots if respect_robots is None else respect_robots
        if should_respect and not self.robots.is_allowed(url):
            raise RobotsDisallowed(url)
        self.rate_limiter.wait(url, sleeper=self.sleeper)
        attempt = 1
        last: HttpResponse | None = None
        while True:
            try:
                response = self._raw_get(url, headers=headers)
            except TransportError:
                if attempt > self.config.max_retries:
                    raise
                self.sleeper(self._backoff(attempt))
                attempt += 1
                continue
            if response.status_code in self.config.retry_statuses and attempt <= self.config.max_retries:
                self.sleeper(self._backoff(attempt))
                attempt += 1
                continue
            last = response
            break
        if last is None:
            raise HttpError(url, message="no response")
        if last.status_code >= 400:
            raise HttpError(
                url,
                status_code=last.status_code,
                message=f"HTTP {last.status_code} from {last.url}",
            )
        return last