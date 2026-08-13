from ..errors import ConfigurationError
from .base import DiscoveryStrategy
from .html import HTMLDiscovery
from .rss import RSSDiscovery
from .sitemap import SitemapDiscovery

STRATEGIES = {
    "rss": RSSDiscovery,
    "sitemap": SitemapDiscovery,
    "html": HTMLDiscovery,
}


def create(source, client, now=None) -> DiscoveryStrategy:
    kind = source.discovery.kind
    strategy_cls = STRATEGIES.get(kind)
    if strategy_cls is None:
        raise ConfigurationError(
            f"Unknown discovery strategy {kind!r} for source {source.id}"
        )
    return strategy_cls(source, client, now=now)


__all__ = [
    "DiscoveryStrategy",
    "RSSDiscovery",
    "SitemapDiscovery",
    "HTMLDiscovery",
    "STRATEGIES",
    "create",
]