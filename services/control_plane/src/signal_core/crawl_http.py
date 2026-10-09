"""Pinned-destination HTTP GET boundary for admitted public crawl URLs."""

import hashlib
import http.client
import ipaddress
import queue
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from signal_core.crawl_urls import (
    CrawlScopePolicy,
    CrawlUrl,
    CrawlUrlRejected,
    validate_public_addresses,
)


class CrawlFetchRejected(ValueError):
    """A request or response violates the deterministic crawl policy."""


class CrawlFetchUnavailable(RuntimeError):
    """Every admitted destination failed without exposing provider details."""


@dataclass(frozen=True, repr=False)
class CrawlFetchResult:
    schema_version: int
    original_url: str
    final_url: str
    normalized_key: str
    outcome: str
    http_status: int
    media_type: str | None
    response_headers: tuple[tuple[str, str], ...]
    redirect_chain: tuple[str, ...]
    resolved_address: str
    body: bytes
    body_sha256: str | None
    decoded_bytes: int
    elapsed_ms: int


@dataclass(frozen=True, repr=False)
class RobotsFetchResult:
    """Bounded robots retrieval evidence; body content is intentionally opaque in repr."""

    schema_version: int
    origin: str
    robots_url: str
    final_url: str
    outcome: str
    http_status: int | None
    media_type: str | None
    response_headers: tuple[tuple[str, str], ...]
    redirect_chain: tuple[str, ...]
    resolved_address: str | None
    body: bytes
    body_sha256: str | None
    decoded_bytes: int
    elapsed_ms: int


TELEGRAM_METHODS = frozenset(
    {"getMe", "setWebhook", "deleteWebhook", "sendMessage", "answerCallbackQuery"}
)


@dataclass(frozen=True, repr=False)
class TelegramBotCredential:
    token: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.token, str)
            or re.fullmatch(r"[A-Za-z0-9_:-]{16,256}", self.token) is None
        ):
            raise ValueError("Invalid Telegram bot credential.")


@dataclass(frozen=True, repr=False)
class EgressHttpRequest:
    """One exact, bounded outbound request; sensitive values stay out of repr."""

    method: str
    url: str
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""
    accepted_media_types: tuple[str, ...] = ("application/json",)
    max_response_bytes: int = 128 * 1024
    timeout_seconds: float = 20.0
    telegram_credential: TelegramBotCredential | None = None

    def __post_init__(self) -> None:
        if self.method not in {"GET", "HEAD", "POST"}:
            raise ValueError("Shared egress permits only GET, HEAD, or POST.")
        if not isinstance(self.url, str):
            raise ValueError("Shared egress requires an exact URL.")
        if self.telegram_credential is not None and (
            not isinstance(self.telegram_credential, TelegramBotCredential)
            or self.method != "POST"
            or self.url not in {"https://api.telegram.org/" + method for method in TELEGRAM_METHODS}
        ):
            raise ValueError("Telegram path credentials require an exact Bot API method.")
        if not isinstance(self.body, bytes) or len(self.body) > _EGRESS_MAX_REQUEST_BYTES:
            raise ValueError("Shared egress request body exceeds its limit.")
        if self.method in {"GET", "HEAD"} and self.body:
            raise ValueError("GET and HEAD shared-egress requests cannot carry a body.")
        if not isinstance(self.headers, tuple) or len(self.headers) > 8:
            raise ValueError("Shared egress request headers are invalid.")
        normalized_headers: list[tuple[str, str]] = []
        seen_headers: set[str] = set()
        for raw_name, raw_value in self.headers:
            if not isinstance(raw_name, str) or not isinstance(raw_value, str):
                raise ValueError("Shared egress request headers are invalid.")
            name = raw_name.lower()
            if (
                name not in _EGRESS_REQUEST_HEADERS
                or name in seen_headers
                or raw_name != name
                or not raw_value
                or len(raw_value) > 4096
                or _HEADER_CONTROL.search(raw_value) is not None
            ):
                raise ValueError("Shared egress request headers are invalid.")
            seen_headers.add(name)
            normalized_headers.append((name, raw_value))
        if self.method == "POST" and "content-type" not in seen_headers:
            raise ValueError("A shared egress POST requires Content-Type.")
        if (
            not isinstance(self.accepted_media_types, tuple)
            or not 1 <= len(self.accepted_media_types) <= 16
        ):
            raise ValueError("Shared egress accepted media types are invalid.")
        normalized_media_types: list[str] = []
        for media_type in self.accepted_media_types:
            if (
                not isinstance(media_type, str)
                or media_type != media_type.lower()
                or _MEDIA_TYPE.fullmatch(media_type) is None
                or media_type in normalized_media_types
            ):
                raise ValueError("Shared egress accepted media types are invalid.")
            normalized_media_types.append(media_type)
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or not 1 <= self.max_response_bytes <= 5 * 1024 * 1024
        ):
            raise ValueError("Shared egress response limit is invalid.")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0.25 <= float(self.timeout_seconds) <= 60.0
        ):
            raise ValueError("Shared egress timeout is invalid.")
        object.__setattr__(self, "headers", tuple(normalized_headers))
        object.__setattr__(self, "accepted_media_types", tuple(normalized_media_types))
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    def request_target(self, url: CrawlUrl) -> str:
        if self.telegram_credential is None:
            return url.request_target
        return "/bot" + self.telegram_credential.token + url.request_target


@dataclass(frozen=True, repr=False)
class EgressHttpResult:
    """Sanitized response evidence plus an opaque bounded body."""

    schema_version: int
    request_url: str
    final_url: str
    method: str
    outcome: str
    http_status: int | None
    media_type: str | None
    response_headers: tuple[tuple[str, str], ...]
    resolved_address: str | None
    body: bytes
    body_sha256: str | None
    decoded_bytes: int
    elapsed_ms: int


AddressResolver = Callable[[str, int, float], Sequence[str]]


class BoundedSystemResolver:
    """Resolve A/AAAA records through the host resolver without blocking callers forever."""

    def __init__(self, *, max_in_flight: int = 8) -> None:
        if not isinstance(max_in_flight, int) or isinstance(max_in_flight, bool):
            raise ValueError("Resolver concurrency must be an integer.")
        if max_in_flight < 1 or max_in_flight > 64:
            raise ValueError("Resolver concurrency must be between 1 and 64.")
        self._capacity = threading.BoundedSemaphore(max_in_flight)

    def __call__(self, host: str, port: int, timeout: float) -> tuple[str, ...]:
        if not isinstance(host, str) or not host or len(host) > 253:
            raise ValueError("A bounded DNS hostname is required.")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("A valid DNS service port is required.")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("A positive DNS timeout is required.")
        bounded_timeout = min(float(timeout), 30.0)
        if not self._capacity.acquire(timeout=bounded_timeout):
            raise TimeoutError("DNS resolver capacity is unavailable.")

        result: queue.Queue[tuple[str, ...] | BaseException] = queue.Queue(maxsize=1)

        def resolve() -> None:
            try:
                records = socket.getaddrinfo(
                    host,
                    port,
                    family=socket.AF_UNSPEC,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                )
                addresses = tuple(dict.fromkeys(record[4][0] for record in records))
                if not addresses:
                    raise OSError("DNS returned no addresses.")
                result.put_nowait(addresses)
            except BaseException as exc:
                result.put_nowait(exc)
            finally:
                self._capacity.release()

        threading.Thread(
            target=resolve,
            name="signal-bounded-dns",
            daemon=True,
        ).start()
        try:
            outcome = result.get(timeout=bounded_timeout)
        except queue.Empty:
            raise TimeoutError("DNS resolution exceeded its timeout.") from None
        if isinstance(outcome, BaseException):
            raise OSError("DNS resolution is unavailable.") from outcome
        return outcome


class PinnedHttpFetcher:
    """Resolve once per hop, validate all answers, and connect to one exact IP."""

    def __init__(
        self,
        resolver: AddressResolver,
        *,
        tls_context: ssl.SSLContext | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(resolver):
            raise ValueError("A controlled crawl resolver is required.")
        if tls_context is not None and not isinstance(tls_context, ssl.SSLContext):
            raise ValueError("A valid crawl TLS context is required.")
        if not callable(clock):
            raise ValueError("A monotonic crawl clock is required.")
        self._resolver = resolver
        self._tls_context = tls_context or ssl.create_default_context()
        self._clock = clock

    def fetch(self, value: object, *, policy: CrawlScopePolicy) -> CrawlFetchResult:
        return self._fetch(
            value,
            policy=policy,
            accept_header=None,
            accepted_media_types=_HTML_MEDIA_TYPES,
        )

    def fetch_text(self, value: object, *, policy: CrawlScopePolicy) -> CrawlFetchResult:
        """Retrieve one bounded plaintext resource through the pinned boundary."""
        return self._fetch(
            value,
            policy=policy,
            accept_header="text/plain",
            accepted_media_types=_PLAIN_TEXT_MEDIA_TYPES,
        )

    def _fetch(
        self,
        value: object,
        *,
        policy: CrawlScopePolicy,
        accept_header: str | None,
        accepted_media_types: frozenset[str],
    ) -> CrawlFetchResult:
        if not isinstance(policy, CrawlScopePolicy):
            raise ValueError("A validated crawl scope policy is required.")
        started = self._clock()
        deadline = started + float(policy.total_timeout_seconds)
        current = policy.admit(value)
        first_original = current.original_url
        visited = {current.normalized_key}
        redirects: list[str] = []

        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise CrawlFetchUnavailable("Crawl request exceeded its total timeout.")
            request_timeout = min(float(policy.request_timeout_seconds), remaining)
            addresses = self._resolve(current, timeout=request_timeout)
            exchange_started = self._clock()
            remaining = deadline - exchange_started
            if remaining <= 0:
                raise CrawlFetchUnavailable("Crawl request exceeded its total timeout.")
            exchange_timeout = min(request_timeout, remaining)
            response, address = self._exchange_any(
                current,
                addresses,
                user_agent=policy.user_agent,
                timeout=exchange_timeout,
                deadline=exchange_started + exchange_timeout,
                accept_header=accept_header,
            )
            try:
                headers = _response_headers(response)
                if response.status in _REDIRECT_STATUSES:
                    location = _single_header(response, "location", required=True)
                    if len(redirects) >= policy.max_redirects:
                        raise CrawlFetchRejected("Crawl redirect limit was exceeded.")
                    try:
                        target = policy.admit_redirect(current, location)
                    except CrawlUrlRejected:
                        raise CrawlFetchRejected("Crawl redirect target was rejected.") from None
                    if target.normalized_key in visited:
                        raise CrawlFetchRejected("Crawl redirect loop was rejected.")
                    visited.add(target.normalized_key)
                    redirects.append(target.fetch_url)
                    current = target
                    continue
                result = _read_final_response(
                    response,
                    current,
                    original_url=first_original,
                    headers=headers,
                    redirect_chain=tuple(redirects),
                    resolved_address=address,
                    max_body_bytes=policy.max_body_bytes,
                    accepted_media_types=accepted_media_types,
                    elapsed_ms=0,
                )
                finished = self._clock()
                if finished > deadline:
                    raise CrawlFetchUnavailable("Crawl request exceeded its total timeout.")
                return replace(result, elapsed_ms=_elapsed_ms(started, finished))
            finally:
                response.close()

    def request(
        self,
        request: EgressHttpRequest,
        *,
        policy: CrawlScopePolicy,
    ) -> EgressHttpResult:
        """Execute one non-redirecting request through the pinned public-peer boundary."""
        if not isinstance(request, EgressHttpRequest):
            raise ValueError("A validated shared egress request is required.")
        if not isinstance(policy, CrawlScopePolicy):
            raise ValueError("A validated crawl scope policy is required.")
        started = self._clock()
        deadline = started + min(
            float(policy.total_timeout_seconds),
            request.timeout_seconds,
        )
        current = policy.admit(request.url)
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise CrawlFetchUnavailable("Shared egress request exceeded its total timeout.")
        request_timeout = min(
            float(policy.request_timeout_seconds),
            request.timeout_seconds,
            remaining,
        )
        addresses = self._resolve(current, timeout=request_timeout)
        exchange_started = self._clock()
        remaining = deadline - exchange_started
        if remaining <= 0:
            raise CrawlFetchUnavailable("Shared egress request exceeded its total timeout.")
        exchange_timeout = min(request_timeout, remaining)
        response, address = self._exchange_egress_any(
            current,
            addresses,
            request=request,
            user_agent=policy.user_agent,
            timeout=exchange_timeout,
            deadline=exchange_started + exchange_timeout,
        )
        try:
            headers = _egress_response_headers(response)
            if response.status in _REDIRECT_STATUSES:
                finished = self._clock()
                if finished > deadline:
                    raise CrawlFetchUnavailable("Shared egress request exceeded its total timeout.")
                return _egress_empty_result(
                    request,
                    current,
                    outcome="redirect_rejected",
                    status=response.status,
                    media_type=_media_type(
                        _single_header(response, "content-type", required=False)
                    ),
                    headers=headers,
                    address=address,
                    elapsed_ms=_elapsed_ms(started, finished),
                )
            result = _read_egress_response(
                response,
                request=request,
                url=current,
                headers=headers,
                resolved_address=address,
                deadline=deadline,
                clock=self._clock,
            )
            finished = self._clock()
            if finished > deadline:
                raise CrawlFetchUnavailable("Shared egress request exceeded its total timeout.")
            return replace(result, elapsed_ms=_elapsed_ms(started, finished))
        except (OSError, http.client.HTTPException):
            raise CrawlFetchUnavailable("Shared egress response became unavailable.") from None
        finally:
            response.close()

    def fetch_robots(self, origin: object, *, policy: CrawlScopePolicy) -> RobotsFetchResult:
        """Retrieve one origin's robots file through the same pinned network boundary."""
        if not isinstance(policy, CrawlScopePolicy):
            raise ValueError("A validated crawl scope policy is required.")
        if not isinstance(origin, str) or origin not in policy.allowed_origins:
            raise CrawlFetchRejected("Robots origin is outside the admitted crawl origins.")
        started = self._clock()
        deadline = started + float(policy.total_timeout_seconds)
        robots_url = f"{origin}/robots.txt"
        current = policy.admit(robots_url)
        visited = {current.normalized_key}
        redirects: list[str] = []

        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                return _robots_empty_result(
                    origin,
                    robots_url,
                    current,
                    outcome="transport_error",
                    redirects=tuple(redirects),
                    elapsed_ms=_elapsed_ms(started, self._clock()),
                )
            request_timeout = min(float(policy.request_timeout_seconds), remaining)
            try:
                addresses = self._resolve(current, timeout=request_timeout)
                exchange_started = self._clock()
                remaining = deadline - exchange_started
                if remaining <= 0:
                    raise CrawlFetchUnavailable()
                exchange_timeout = min(request_timeout, remaining)
                response, address = self._exchange_any(
                    current,
                    addresses,
                    user_agent=policy.user_agent,
                    timeout=exchange_timeout,
                    deadline=exchange_started + exchange_timeout,
                    accept_header=_ROBOTS_ACCEPT,
                )
            except CrawlFetchRejected:
                return _robots_empty_result(
                    origin,
                    robots_url,
                    current,
                    outcome="policy_rejected",
                    redirects=tuple(redirects),
                    elapsed_ms=_elapsed_ms(started, self._clock()),
                )
            except CrawlFetchUnavailable:
                return _robots_empty_result(
                    origin,
                    robots_url,
                    current,
                    outcome="transport_error",
                    redirects=tuple(redirects),
                    elapsed_ms=_elapsed_ms(started, self._clock()),
                )

            try:
                headers = _response_headers(response)
                if response.status in _REDIRECT_STATUSES:
                    try:
                        location = _single_header(response, "location", required=True)
                        if len(redirects) >= min(policy.max_redirects, 5):
                            raise CrawlFetchRejected()
                        target = policy.admit_redirect(current, location)
                        if target.normalized_key in visited:
                            raise CrawlFetchRejected()
                    except (CrawlFetchRejected, CrawlUrlRejected):
                        return _robots_empty_result(
                            origin,
                            robots_url,
                            current,
                            outcome="policy_rejected",
                            status=response.status,
                            media_type=_media_type(
                                _single_header(response, "content-type", required=False)
                            ),
                            headers=headers,
                            redirects=tuple(redirects),
                            address=address,
                            elapsed_ms=_elapsed_ms(started, self._clock()),
                        )
                    visited.add(target.normalized_key)
                    redirects.append(target.fetch_url)
                    current = target
                    continue
                result = _read_robots_response(
                    response,
                    origin=origin,
                    robots_url=robots_url,
                    current=current,
                    headers=headers,
                    redirects=tuple(redirects),
                    address=address,
                )
                finished = self._clock()
                if finished > deadline:
                    return replace(
                        result,
                        outcome="transport_error",
                        body=b"",
                        body_sha256=None,
                        decoded_bytes=0,
                        elapsed_ms=_elapsed_ms(started, finished),
                    )
                return replace(result, elapsed_ms=_elapsed_ms(started, finished))
            except CrawlFetchRejected:
                return _robots_empty_result(
                    origin,
                    robots_url,
                    current,
                    outcome="policy_rejected",
                    status=response.status if 100 <= response.status <= 599 else None,
                    redirects=tuple(redirects),
                    address=address,
                    elapsed_ms=_elapsed_ms(started, self._clock()),
                )
            except CrawlFetchUnavailable:
                return _robots_empty_result(
                    origin,
                    robots_url,
                    current,
                    outcome="transport_error",
                    status=response.status if 100 <= response.status <= 599 else None,
                    headers=headers,
                    redirects=tuple(redirects),
                    address=address,
                    elapsed_ms=_elapsed_ms(started, self._clock()),
                )
            finally:
                response.close()

    def _resolve(self, url: CrawlUrl, *, timeout: float) -> tuple[str, ...]:
        try:
            answers = self._resolver(url.host, url.port, timeout)
            return validate_public_addresses(tuple(answers))
        except CrawlUrlRejected:
            raise CrawlFetchRejected("Crawl destination resolution was rejected.") from None
        except Exception:
            raise CrawlFetchUnavailable("Crawl destination resolution is unavailable.") from None

    def _exchange_any(
        self,
        url: CrawlUrl,
        addresses: tuple[str, ...],
        *,
        user_agent: str,
        timeout: float,
        deadline: float,
        accept_header: str | None = None,
    ) -> tuple[http.client.HTTPResponse, str]:
        for index, address in enumerate(addresses):
            if index:
                timeout = min(timeout, deadline - self._clock())
                if timeout <= 0:
                    raise CrawlFetchUnavailable("Crawl destination is unavailable.")
            try:
                arguments = {"user_agent": user_agent, "timeout": timeout}
                if accept_header is not None:
                    arguments["accept_header"] = accept_header
                return self._exchange(url, address, **arguments), address
            except (OSError, ssl.SSLError, http.client.HTTPException):
                continue
        raise CrawlFetchUnavailable("Crawl destination is unavailable.")

    def _exchange_egress_any(
        self,
        url: CrawlUrl,
        addresses: tuple[str, ...],
        *,
        request: EgressHttpRequest,
        user_agent: str,
        timeout: float,
        deadline: float,
    ) -> tuple[http.client.HTTPResponse, str]:
        candidates = addresses[:1] if request.method == "POST" else addresses
        for index, address in enumerate(candidates):
            if index:
                timeout = min(timeout, deadline - self._clock())
                if timeout <= 0:
                    raise CrawlFetchUnavailable("Shared egress destination is unavailable.")
            try:
                return (
                    self._exchange_egress(
                        url,
                        address,
                        request=request,
                        user_agent=user_agent,
                        timeout=timeout,
                    ),
                    address,
                )
            except (OSError, ssl.SSLError, http.client.HTTPException):
                continue
        raise CrawlFetchUnavailable("Shared egress destination is unavailable.")

    def _exchange(
        self,
        url: CrawlUrl,
        address: str,
        *,
        user_agent: str,
        timeout: float,
        accept_header: str = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
    ) -> http.client.HTTPResponse:
        connection = http.client.HTTPConnection(url.host, url.port, timeout=timeout)
        raw_socket: socket.socket | None = None
        try:
            raw_socket = socket.create_connection((address, url.port), timeout=timeout)
            raw_socket.settimeout(timeout)
            peer = raw_socket.getpeername()[0]
            if not _same_address(peer, address):
                raise CrawlFetchRejected("Connected crawl destination did not match admission.")
            if url.scheme == "https":
                raw_socket = self._tls_context.wrap_socket(raw_socket, server_hostname=url.host)
            connection.sock = raw_socket
            raw_socket = None
            connection.putrequest(
                "GET",
                url.request_target,
                skip_host=True,
                skip_accept_encoding=True,
            )
            connection.putheader("Host", url.origin.split("://", 1)[1])
            connection.putheader("User-Agent", user_agent)
            connection.putheader("Accept", accept_header)
            connection.putheader("Accept-Encoding", "identity")
            connection.putheader("Connection", "close")
            connection.endheaders()
            return connection.getresponse()
        except Exception:
            connection.close()
            if raw_socket is not None:
                raw_socket.close()
            raise

    def _exchange_egress(
        self,
        url: CrawlUrl,
        address: str,
        *,
        request: EgressHttpRequest,
        user_agent: str,
        timeout: float,
    ) -> http.client.HTTPResponse:
        connection = http.client.HTTPConnection(url.host, url.port, timeout=timeout)
        if request.telegram_credential is not None:
            connection.set_debuglevel(0)
        raw_socket: socket.socket | None = None
        try:
            raw_socket = socket.create_connection((address, url.port), timeout=timeout)
            raw_socket.settimeout(timeout)
            peer = raw_socket.getpeername()[0]
            if not _same_address(peer, address):
                raise CrawlFetchRejected("Connected egress destination did not match admission.")
            if url.scheme == "https":
                raw_socket = self._tls_context.wrap_socket(raw_socket, server_hostname=url.host)
            connection.sock = raw_socket
            raw_socket = None
            connection.putrequest(
                request.method,
                request.request_target(url),
                skip_host=True,
                skip_accept_encoding=True,
            )
            connection.putheader("Host", url.origin.split("://", 1)[1])
            connection.putheader("User-Agent", user_agent)
            for name, value in request.headers:
                connection.putheader(name, value)
            connection.putheader("Accept-Encoding", "identity")
            connection.putheader("Connection", "close")
            if request.method == "POST":
                connection.putheader("Content-Length", str(len(request.body)))
            connection.endheaders(request.body or None)
            timer = threading.Timer(timeout, connection.close)
            timer.daemon = True
            timer.start()
            try:
                return connection.getresponse()
            finally:
                timer.cancel()
        except Exception:
            connection.close()
            if raw_socket is not None:
                raw_socket.close()
            raise


def _read_egress_response(
    response: http.client.HTTPResponse,
    *,
    request: EgressHttpRequest,
    url: CrawlUrl,
    headers: tuple[tuple[str, str], ...],
    resolved_address: str,
    deadline: float,
    clock: Callable[[], float],
) -> EgressHttpResult:
    if not 100 <= response.status <= 599:
        raise CrawlFetchRejected("Shared egress response status was invalid.")
    if response.status == 304:
        raise CrawlFetchRejected("Shared egress cannot use 304 without retained evidence.")
    content_length = _content_length(response)
    encoding = _single_header(response, "content-encoding", required=False)
    media_type = _media_type(_single_header(response, "content-type", required=False))
    if encoding is not None and encoding.strip().lower() not in {"", "identity"}:
        return _egress_empty_result(
            request,
            url,
            outcome="unsupported_encoding",
            status=response.status,
            media_type=media_type,
            headers=headers,
            address=resolved_address,
        )
    if content_length is not None and content_length > request.max_response_bytes:
        return _egress_empty_result(
            request,
            url,
            outcome="body_limit",
            status=response.status,
            media_type=media_type,
            headers=headers,
            address=resolved_address,
        )
    if media_type not in request.accepted_media_types:
        return _egress_empty_result(
            request,
            url,
            outcome="unsupported_media_type",
            status=response.status,
            media_type=media_type,
            headers=headers,
            address=resolved_address,
        )
    if request.method == "HEAD":
        body = b""
    else:
        body = _read_egress_body(
            response,
            max_bytes=request.max_response_bytes,
            deadline=deadline,
            clock=clock,
        )
        if len(body) > request.max_response_bytes:
            return _egress_empty_result(
                request,
                url,
                outcome="body_limit",
                status=response.status,
                media_type=media_type,
                headers=headers,
                address=resolved_address,
            )
    if request.method != "HEAD" and content_length is not None and len(body) != content_length:
        raise CrawlFetchUnavailable("Shared egress response body was incomplete.")
    return EgressHttpResult(
        schema_version=1,
        request_url=request.url,
        final_url=url.fetch_url,
        method=request.method,
        outcome="fetched",
        http_status=response.status,
        media_type=media_type,
        response_headers=headers,
        resolved_address=resolved_address,
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
        decoded_bytes=len(body),
        elapsed_ms=0,
    )


def _read_egress_body(
    response: http.client.HTTPResponse,
    *,
    max_bytes: int,
    deadline: float,
    clock: Callable[[], float],
) -> bytes:
    body = bytearray()
    read_once = getattr(response, "read1", None)
    while len(body) <= max_bytes:
        remaining = deadline - clock()
        if remaining <= 0:
            raise CrawlFetchUnavailable("Shared egress request exceeded its total timeout.")
        _set_response_timeout(response, remaining)
        limit = min(64 * 1024, max_bytes + 1 - len(body))
        chunk = read_once(limit) if callable(read_once) else response.read(limit)
        if clock() > deadline:
            raise CrawlFetchUnavailable("Shared egress request exceeded its total timeout.")
        if not chunk:
            break
        body.extend(chunk)
        if not callable(read_once):
            break
    return bytes(body)


def _set_response_timeout(response: http.client.HTTPResponse, timeout: float) -> None:
    stream = getattr(response, "fp", None)
    raw = getattr(stream, "raw", None)
    peer = getattr(raw, "_sock", None)
    if isinstance(peer, socket.socket):
        peer.settimeout(timeout)


def _egress_empty_result(
    request: EgressHttpRequest,
    url: CrawlUrl,
    *,
    outcome: str,
    status: int | None = None,
    media_type: str | None = None,
    headers: tuple[tuple[str, str], ...] = (),
    address: str | None = None,
    elapsed_ms: int = 0,
) -> EgressHttpResult:
    return EgressHttpResult(
        schema_version=1,
        request_url=request.url,
        final_url=url.fetch_url,
        method=request.method,
        outcome=outcome,
        http_status=status,
        media_type=media_type,
        response_headers=headers,
        resolved_address=address,
        body=b"",
        body_sha256=None,
        decoded_bytes=0,
        elapsed_ms=elapsed_ms,
    )


def _read_robots_response(
    response: http.client.HTTPResponse,
    *,
    origin: str,
    robots_url: str,
    current: CrawlUrl,
    headers: tuple[tuple[str, str], ...],
    redirects: tuple[str, ...],
    address: str,
) -> RobotsFetchResult:
    status = response.status
    if not 100 <= status <= 599:
        raise CrawlFetchRejected()
    media_type = _media_type(_single_header(response, "content-type", required=False))
    if not 200 <= status <= 299:
        outcome = _robots_status_outcome(status)
        return _robots_empty_result(
            origin,
            robots_url,
            current,
            outcome=outcome,
            status=status,
            media_type=media_type,
            headers=headers,
            redirects=redirects,
            address=address,
        )
    content_length = _content_length(response)
    encoding = _single_header(response, "content-encoding", required=False)
    if encoding is not None and encoding.strip().lower() not in {"", "identity"}:
        return _robots_empty_result(
            origin,
            robots_url,
            current,
            outcome="unsupported_encoding",
            status=status,
            media_type=media_type,
            headers=headers,
            redirects=redirects,
            address=address,
        )
    if media_type != "text/plain":
        return _robots_empty_result(
            origin,
            robots_url,
            current,
            outcome="unsupported_media_type",
            status=status,
            media_type=media_type,
            headers=headers,
            redirects=redirects,
            address=address,
        )
    if content_length is not None and content_length > _ROBOTS_MAX_BODY_BYTES:
        return _robots_empty_result(
            origin,
            robots_url,
            current,
            outcome="body_limit",
            status=status,
            media_type=media_type,
            headers=headers,
            redirects=redirects,
            address=address,
        )
    body = response.read(_ROBOTS_MAX_BODY_BYTES + 1)
    if len(body) > _ROBOTS_MAX_BODY_BYTES:
        return _robots_empty_result(
            origin,
            robots_url,
            current,
            outcome="body_limit",
            status=status,
            media_type=media_type,
            headers=headers,
            redirects=redirects,
            address=address,
        )
    if content_length is not None and len(body) != content_length:
        raise CrawlFetchUnavailable()
    return RobotsFetchResult(
        schema_version=1,
        origin=origin,
        robots_url=robots_url,
        final_url=current.fetch_url,
        outcome="fetched",
        http_status=status,
        media_type=media_type,
        response_headers=headers,
        redirect_chain=redirects,
        resolved_address=address,
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
        decoded_bytes=len(body),
        elapsed_ms=0,
    )


def _robots_status_outcome(status: int) -> str:
    if 100 <= status <= 199:
        return "transport_error"
    if 300 <= status <= 399:
        return "policy_rejected"
    if status == 404:
        return "not_found"
    if status in {401, 403}:
        return "forbidden"
    if status == 429:
        return "backoff"
    if 500 <= status <= 599:
        return "server_error"
    return "client_error"


def _robots_empty_result(
    origin: str,
    robots_url: str,
    current: CrawlUrl,
    *,
    outcome: str,
    status: int | None = None,
    media_type: str | None = None,
    headers: tuple[tuple[str, str], ...] = (),
    redirects: tuple[str, ...] = (),
    address: str | None = None,
    elapsed_ms: int = 0,
) -> RobotsFetchResult:
    return RobotsFetchResult(
        schema_version=1,
        origin=origin,
        robots_url=robots_url,
        final_url=current.fetch_url,
        outcome=outcome,
        http_status=status,
        media_type=media_type,
        response_headers=headers,
        redirect_chain=redirects,
        resolved_address=address,
        body=b"",
        body_sha256=None,
        decoded_bytes=0,
        elapsed_ms=elapsed_ms,
    )


def _read_final_response(
    response: http.client.HTTPResponse,
    url: CrawlUrl,
    *,
    original_url: str,
    headers: tuple[tuple[str, str], ...],
    redirect_chain: tuple[str, ...],
    resolved_address: str,
    max_body_bytes: int,
    accepted_media_types: frozenset[str],
    elapsed_ms: int,
) -> CrawlFetchResult:
    if not 100 <= response.status <= 599:
        raise CrawlFetchRejected("Crawl response status was invalid.")
    if response.status == 304:
        raise CrawlFetchRejected("Crawl response cannot use 304 without a retained prior body.")
    content_length = _content_length(response)
    encoding = _single_header(response, "content-encoding", required=False)
    media_type = _media_type(_single_header(response, "content-type", required=False))
    if encoding is not None and encoding.strip().lower() not in {"", "identity"}:
        return _result(
            url,
            original_url=original_url,
            outcome="unsupported_encoding",
            status=response.status,
            media_type=media_type,
            headers=headers,
            redirects=redirect_chain,
            address=resolved_address,
            elapsed_ms=elapsed_ms,
        )
    if content_length is not None and content_length > max_body_bytes:
        return _result(
            url,
            original_url=original_url,
            outcome="body_limit",
            status=response.status,
            media_type=media_type,
            headers=headers,
            redirects=redirect_chain,
            address=resolved_address,
            elapsed_ms=elapsed_ms,
        )
    if media_type not in accepted_media_types:
        return _result(
            url,
            original_url=original_url,
            outcome="unsupported_media_type",
            status=response.status,
            media_type=media_type,
            headers=headers,
            redirects=redirect_chain,
            address=resolved_address,
            elapsed_ms=elapsed_ms,
        )
    body = response.read(max_body_bytes + 1)
    if len(body) > max_body_bytes:
        return _result(
            url,
            original_url=original_url,
            outcome="body_limit",
            status=response.status,
            media_type=media_type,
            headers=headers,
            redirects=redirect_chain,
            address=resolved_address,
            elapsed_ms=elapsed_ms,
        )
    if content_length is not None and len(body) != content_length:
        raise CrawlFetchUnavailable("Crawl response body was incomplete.")
    return CrawlFetchResult(
        schema_version=1,
        original_url=original_url,
        final_url=url.fetch_url,
        normalized_key=url.normalized_key,
        outcome="fetched",
        http_status=response.status,
        media_type=media_type,
        response_headers=headers,
        redirect_chain=redirect_chain,
        resolved_address=resolved_address,
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
        decoded_bytes=len(body),
        elapsed_ms=elapsed_ms,
    )


def _result(
    url: CrawlUrl,
    *,
    original_url: str,
    outcome: str,
    status: int,
    media_type: str | None,
    headers: tuple[tuple[str, str], ...],
    redirects: tuple[str, ...],
    address: str,
    elapsed_ms: int,
) -> CrawlFetchResult:
    return CrawlFetchResult(
        schema_version=1,
        original_url=original_url,
        final_url=url.fetch_url,
        normalized_key=url.normalized_key,
        outcome=outcome,
        http_status=status,
        media_type=media_type,
        response_headers=headers,
        redirect_chain=redirects,
        resolved_address=address,
        body=b"",
        body_sha256=None,
        decoded_bytes=0,
        elapsed_ms=elapsed_ms,
    )


def _response_headers(response: http.client.HTTPResponse) -> tuple[tuple[str, str], ...]:
    captured: dict[str, list[str]] = {}
    total = 0
    for name, value in response.getheaders():
        lower = name.lower()
        if lower not in _RETAINED_HEADERS:
            continue
        if (
            not value
            or len(value) > 2048
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise CrawlFetchRejected("Crawl response header was invalid.")
        total += len(lower) + len(value)
        if total > 16384:
            raise CrawlFetchRejected("Crawl response headers exceeded their limit.")
        captured.setdefault(lower, []).append(value)
    return tuple((name, ", ".join(values)) for name, values in sorted(captured.items()))


def _egress_response_headers(
    response: http.client.HTTPResponse,
) -> tuple[tuple[str, str], ...]:
    return tuple((name, value) for name, value in _response_headers(response) if name != "location")


def _single_header(
    response: http.client.HTTPResponse,
    name: str,
    *,
    required: bool,
) -> str | None:
    values = response.headers.get_all(name, [])
    if len(values) > 1 or (required and len(values) != 1):
        raise CrawlFetchRejected("Crawl response header shape was invalid.")
    if not values:
        return None
    value = values[0]
    if (
        not value
        or len(value) > 2048
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise CrawlFetchRejected("Crawl response header was invalid.")
    return value


def _content_length(response: http.client.HTTPResponse) -> int | None:
    value = _single_header(response, "content-length", required=False)
    transfer_encoding = _single_header(response, "transfer-encoding", required=False)
    if value is not None and transfer_encoding is not None:
        raise CrawlFetchRejected("Ambiguous crawl response framing was rejected.")
    if transfer_encoding is not None and transfer_encoding.strip().lower() != "chunked":
        raise CrawlFetchRejected("Unsupported crawl response framing was rejected.")
    if value is None:
        return None
    if not value.isascii() or not value.isdigit():
        raise CrawlFetchRejected("Crawl content length was invalid.")
    converted = int(value)
    if converted > 2**63 - 1:
        raise CrawlFetchRejected("Crawl content length was invalid.")
    return converted


def _media_type(value: str | None) -> str | None:
    if value is None:
        return None
    media_type = value.split(";", 1)[0].strip().lower()
    if not media_type or len(media_type) > 100:
        raise CrawlFetchRejected("Crawl media type was invalid.")
    return media_type


def _same_address(actual: str, expected: str) -> bool:
    try:
        actual_ip = ipaddress.ip_address(actual)
        expected_ip = ipaddress.ip_address(expected)
    except ValueError:
        return False
    actual_checked = (
        actual_ip.ipv4_mapped or actual_ip
        if isinstance(actual_ip, ipaddress.IPv6Address)
        else actual_ip
    )
    expected_checked = (
        expected_ip.ipv4_mapped or expected_ip
        if isinstance(expected_ip, ipaddress.IPv6Address)
        else expected_ip
    )
    return actual_checked == expected_checked


def _elapsed_ms(started: float, finished: float) -> int:
    elapsed = max(0.0, finished - started)
    return min(int(elapsed * 1000), 2**31 - 1)


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_PLAIN_TEXT_MEDIA_TYPES = frozenset({"text/plain"})
_ROBOTS_ACCEPT = "text/plain,*/*;q=0.1"
_ROBOTS_MAX_BODY_BYTES = 500 * 1024
_EGRESS_MAX_REQUEST_BYTES = 1024 * 1024
_EGRESS_REQUEST_HEADERS = frozenset(
    {"accept", "authorization", "content-type", "x-github-api-version", "x-goog-api-key"}
)
_HEADER_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MEDIA_TYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}")
_RETAINED_HEADERS = {
    "cache-control",
    "content-encoding",
    "content-length",
    "content-type",
    "etag",
    "last-modified",
    "location",
    "retry-after",
    "transfer-encoding",
}
