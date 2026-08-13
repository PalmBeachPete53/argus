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


class InvalidFixture(ArgusError):
    pass


class ConfigurationError(ArgusError):
    pass