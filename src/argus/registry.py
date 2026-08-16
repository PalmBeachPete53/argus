from __future__ import annotations

from . import models
from .adapters import ALL_ADAPTERS, BankAdapter
from .config import is_bank_enabled


class SourceRegistry:
    def __init__(self, adapters: list[BankAdapter] | None = None) -> None:
        self.adapters = list(adapters) if adapters is not None else list(ALL_ADAPTERS)
        self._banks: dict[str, models.CentralBank] = {
            adapter.bank.id: adapter.bank for adapter in self.adapters
        }
        bank_order = {bank_id: i for i, bank_id in enumerate(self._banks)}
        self._sources: list[models.Source] = []
        for adapter in self.adapters:
            self._sources.extend(adapter.sources)
        self._sources.sort(key=lambda s: (bank_order.get(s.central_bank, 99), s.priority))

    @property
    def banks(self) -> list[models.CentralBank]:
        """Every registered bank, including disabled ones (they remain known)."""
        return [self._banks[b] for b in self._banks]

    @property
    def active_banks(self) -> list[models.CentralBank]:
        """Banks that currently participate in operational executions."""
        return [b for b in self._banks.values() if is_bank_enabled(b.id)]

    def bank(self, bank_id: str) -> models.CentralBank | None:
        return self._banks.get(bank_id)

    @property
    def sources(self) -> list[models.Source]:
        return self._sources

    def sources_for_bank(self, bank_id: str) -> list[models.Source]:
        return [s for s in self._sources if s.central_bank == bank_id]

    def source(self, source_id: str) -> models.Source | None:
        for source in self._sources:
            if source.id == source_id:
                return source
        return None

    def enabled_sources(
        self,
        *,
        banks: tuple[str, ...] | list[str] | None = None,
        source_ids: tuple[str, ...] | list[str] | None = None,
    ) -> list[models.Source]:
        selected = []
        for source in self._sources:
            if banks is not None and source.central_bank not in banks:
                continue
            if source_ids is not None and source.id not in source_ids:
                continue
            if not source.enabled:
                continue
            if not is_bank_enabled(source.central_bank):
                continue
            selected.append(source)
        return selected