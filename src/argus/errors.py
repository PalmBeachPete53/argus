class ArgusError(Exception):
    pass


class HttpError(ArgusError):
    def __init__(self, url, status_code=None, message=None, cause=None):
        self.url = url
        self.status_code = status_code
        self.message = message or str(cause) if cause else message
        super().__init__(
            f"{self.__class__.__name__}(url={url}, status={status_code}, message={self.message})"
        )
        self.cause = cause


class TransportError(ArgusError):
    def __init__(self, url, message):
        self.url = url
        super().__init__(f"{self.__class__.__name__}(url={url}, message={message})")


class RobotsDisallowed(ArgusError):
    def __init__(self, url):
        self.url = url
        super().__init__(f"robots.txt disallows fetching: {url}")


class DiscoveryError(ArgusError):
    def __init__(self, source_id, strategy, url, message):
        self.source_id = source_id
        self.strategy = strategy
        self.url = url
        self.message = message
        super().__init__(
            f"DiscoveryError(source={source_id}, strategy={strategy}, url={url}, message={message})"
        )


class FetchError(ArgusError):
    pass


class InvalidDocumentContent(FetchError):
    """A fetched body is not a valid document for its expected kind.

    Raised by the Fetcher when a minimal content sanity check fails — an empty
    ``200`` body, an HTML challenge / bot page served in place of a real
    document, or a declared content type that contradicts the bytes actually
    received. The check is deliberately conservative: a server using an imprecise
    MIME never gets rejected on its own.

    A rejected response follows the normal error path (``DocumentStatus.FAILED``
    + the publication becomes ``PARTIAL`` / ``FAILED``), never
    ``DocumentStatus.FETCHED``.
    """

    def __init__(self, url, kind, message):
        self.url = url
        self.kind = kind
        self.message = message
        super().__init__(f"InvalidDocumentContent(kind={kind}, url={url}, message={message})")


class InvalidFixture(ArgusError):
    pass


class ConfigurationError(ArgusError):
    pass