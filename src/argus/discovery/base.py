from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Callable

from .. import models
from ..normalize import now_utc, title_from_url


class DiscoveryStrategy(ABC):
    kind: str = ""

    def __init__(self, source: models.Source, client, now: Callable | None = None) -> None:
        self.source = source
        self.spec = source.discovery
        self.client = client
        self._now = now or now_utc
        self.now = self._now()

    def discover(self) -> list[models.Publication]:  # pragma: no cover - abstract
        raise NotImplementedError

    def _allowed(self, url: str) -> bool:
        if self.spec.include:
            included = any(re.search(p, url, re.IGNORECASE) for p in self.spec.include)
            if not included:
                return False
        for pattern in self.spec.exclude:
            if re.search(pattern, url, re.IGNORECASE):
                return False
        if self.spec.scope_prefixes:
            if not any(url.startswith(prefix) for prefix in self.spec.scope_prefixes):
                return False
        return True

    def _in_window(self, publication_date) -> bool:
        if publication_date is None:
            return True
        if publication_date.tzinfo is None:
            return True
        if publication_date > self.now and not self.spec.allow_future:
            return False
        if self.spec.lookback_window_days is not None:
            cutoff = self.now - timedelta(days=self.spec.lookback_window_days)
            if publication_date < cutoff:
                return False
        return True

    def _make(
        self,
        *,
        url: str,
        title: str | None = None,
        publication_date=None,
        meeting_date=None,
        publication_type: str | None = None,
        document_urls=(),
        language: str | None = None,
        extra: dict | None = None,
    ) -> models.Publication:
        url = url or ""
        type_hint = self.source.publication_types
        metadata = dict(extra or {})
        if type_hint and "type_hint" not in metadata:
            metadata["type_hint"] = list(type_hint)
        return models.Publication(
            central_bank=self.source.central_bank,
            title=(title or title_from_url(url)).strip(),
            url=url,
            source_id=self.source.id,
            source_url=self.spec.url,
            publication_date=publication_date,
            meeting_date=meeting_date,
            publication_type=publication_type,
            language=language,
            document_urls=tuple(dict.fromkeys(doc for doc in document_urls if doc)),
            extra=metadata,
        )