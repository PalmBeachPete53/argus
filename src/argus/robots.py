from __future__ import annotations

import threading
import time
from urllib.parse import urljoin, urlparse

from .errors import HttpError, TransportError

_ALLOW_BY_DEFAULT = "*"


class RobotsRuleSet:
    def __init__(self) -> None:
        self.disallow: dict[str, list[str]] = {}
        self.crawl_delay: dict[str, float] = {}
        self.default_group = _ALLOW_BY_DEFAULT

    @classmethod
    def from_text(cls, body: str) -> "RobotsRuleSet":
        rules = cls()
        current_group: str | None = None
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "#" in line:
                line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                current_group = value.lower()
                rules.disallow.setdefault(current_group, [])
            elif key == "disallow":
                if current_group is not None:
                    rules.disallow.setdefault(current_group, []).append(value)
            elif key == "allow":
                if current_group is not None:
                    rules.disallow.setdefault(current_group, []).append(value or "")
            elif key == "crawl-delay":
                try:
                    rules.crawl_delay[current_group or _ALLOW_BY_DEFAULT] = float(value)
                except ValueError:
                    pass
        return rules

    def effective_disallow(self, agent_token: str) -> list[str]:
        group = agent_token.lower() if agent_token in self.disallow else "*"
        return self.disallow.get(group, self.disallow.get(_ALLOW_BY_DEFAULT, []))


def path_allowed(path: str, disallow: list[str]) -> bool:
    allowed = ""
    for rule in disallow:
        if not rule:
            if not allowed:
                return False
            continue
        if path.startswith(rule):
            if len(rule) >= len(allowed):
                allowed = rule
    return not bool(allowed)


class RobotsGate:
    def __init__(
        self,
        getter,
        *,
        token: str = "argus",
        ttl_seconds: float = 86400.0,
        on_error: str = "allow",
    ) -> None:
        self._getter = getter
        self.token = token
        self.ttl = ttl_seconds
        self.on_error = on_error
        if on_error not in ("allow", "deny"):
            raise ValueError(f"on_error must be 'allow' or 'deny', got {on_error!r}")
        self._cache: dict[str, tuple[float, RobotsRuleSet | None]] = {}
        self._lock = threading.Lock()

    def _fetch(self, origin: str) -> RobotsRuleSet | None:
        url = urljoin(f"https://{origin}/", "robots.txt")
        try:
            response = self._getter(url)
            if response.status_code != 200 or not response.text:
                return None
            return RobotsRuleSet.from_text(response.text)
        except (HttpError, TransportError):
            return None

    def _rules(self, origin: str) -> RobotsRuleSet | None:
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(origin)
            if cached and now - cached[0] < self.ttl:
                return cached[1]
        rules = self._fetch(origin)
        with self._lock:
            self._cache[origin] = (now, rules)
        return rules

    def is_allowed(self, url: str, *, user_agent: str = "") -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.hostname}:{parsed.port or ''}" if parsed.port else (parsed.hostname or "")
        origin = origin.rstrip(":").lower()
        if not origin:
            return True
        rules = self._rules(origin)
        if rules is None:
            return self.on_error == "allow"
        disallow = rules.effective_disallow(self.token)
        return path_allowed(parsed.path, disallow)

    def crawl_delay(self, url: str) -> float:
        parsed = urlparse(url)
        origin = (parsed.hostname or "").lower()
        rules = self._rules(origin)
        if rules is None:
            return 0.0
        token = self.token if self.token in rules.crawl_delay else _ALLOW_BY_DEFAULT
        return rules.crawl_delay.get(token, 0.0)