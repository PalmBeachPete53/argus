from . import models
from .classification import (
    Confidence,
    PublicationClassification,
    PublicationClassifier,
    canonical_types,
)
from .collector import CentralBankCollector, DEFAULT_RAW_ROOT, DEFAULT_STORE_PATH
from .documents import (
    DocumentParser,
    DocumentSection,
    DocumentTable,
    NormalizedDocument,
    Normalizer,
    ParserRegistry,
    document_id_of,
)
from .errors import (
    ArgusError,
    ConfigurationError,
    DiscoveryError,
    FetchError,
    HttpError,
    RobotsDisallowed,
    TransportError,
)
from .fetcher import Fetcher
from .http import HttpClient, HttpConfig
from .models import (
    CentralBank,
    CollectError,
    DiscoverySpec,
    Document,
    FetchResult,
    Publication,
    PublicationStatus,
    RunResult,
    Source,
)
from .registry import SourceRegistry
from .store import Store

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "models",
    "CentralBankCollector",
    "CentralBank",
    "Source",
    "DiscoverySpec",
    "Publication",
    "Document",
    "FetchResult",
    "RunResult",
    "CollectError",
    "PublicationStatus",
    "SourceRegistry",
    "Store",
    "Fetcher",
    "HttpClient",
    "HttpConfig",
    # Phase 2A — normalization
    "Normalizer",
    "NormalizedDocument",
    "DocumentSection",
    "DocumentTable",
    "DocumentParser",
    "ParserRegistry",
    "document_id_of",
    # Phase 2B — classification
    "PublicationClassifier",
    "PublicationClassification",
    "Confidence",
    "canonical_types",
    "ArgusError",
    "HttpError",
    "TransportError",
    "RobotsDisallowed",
    "DiscoveryError",
    "FetchError",
    "ConfigurationError",
    "DEFAULT_STORE_PATH",
    "DEFAULT_RAW_ROOT",
]