"""
Copyright (C) 2026, Pelican Project, Morgridge Institute for Research

Licensed under the Apache License, Version 2.0 (the "License"); you
may not use this file except in compliance with the License.  You may
obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

__lazy_modules__ = {"collections.abc", "types", "urllib.parse"}

import enum
import threading
import time
import urllib.parse
from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace

import aiohttp
from fsspec.callbacks import Callback, NoOpCallback


class ResponseFailure(enum.Enum):
    """Stands in for the status in `TransferStats.responses` when no status tells the result."""

    NO_RESPONSE = "no response"
    BODY = "body"


def _host_of(url: object) -> str:
    """Strip a URL down to scheme://host, the identity of a cache."""
    return urllib.parse.urlparse(str(url))._replace(path="", query="", fragment="").geturl()


@dataclass(frozen=True)
class TransferStats:
    """
    Totals for one filesystem object, from its creation to the time of the call.

    A request is one HTTP request, so each cache attempt is a request of its own. An
    operation is one call that moves data: cat_file, get_file, put_file, or read on an
    open file. An operation fails only when no retry could save it. A missing object
    (404) is not a failure of either kind.

    `responses` counts HTTP responses by (server, method, status), with the server as
    `scheme://host:port`. Each redirect hop is a response. It covers every server, not only
    caches. A request that got no response has `ResponseFailure.NO_RESPONSE` for its status.
    A body that broke off adds a `ResponseFailure.BODY` entry for "GET", beside the status
    that arrived with its headers.

    `cache_busy_seconds` is the read time by cache, with failed attempts included. An
    upload has no cache, and a direct read appears under its origin.

    `cache_bytes_read` is the bytes read by cache, with those of a failed `get_file`
    attempt included.
    """

    requests: int = 0
    failed_requests: int = 0
    operations: int = 0
    failed_operations: int = 0
    bytes_read: int = 0
    bytes_written: int = 0
    busy_seconds: float = 0.0
    responses: Counter[tuple[str, str, int | ResponseFailure]] = field(default_factory=Counter, hash=False)
    cache_busy_seconds: dict[str, float] = field(default_factory=dict, hash=False)
    cache_bytes_read: Counter[str] = field(default_factory=Counter, hash=False)

    @property
    def request_success_rate(self) -> float | None:
        """The fraction of requests that succeeded, or None before the first request."""
        return 1 - self.failed_requests / self.requests if self.requests else None

    @property
    def operation_success_rate(self) -> float | None:
        """The fraction of operations that succeeded, or None before the first operation."""
        return 1 - self.failed_operations / self.operations if self.operations else None

    @property
    def average_rate(self) -> float | None:
        """Bytes per second while an operation was active, or None if no time was measured."""
        return (self.bytes_read + self.bytes_written) / self.busy_seconds if self.busy_seconds else None

    @property
    def cache_read_rates(self) -> dict[str, float]:
        """Bytes per second by cache while the cache was reading, for each cache with measured time."""
        return {cache: self.cache_bytes_read[cache] / seconds for cache, seconds in self.cache_busy_seconds.items() if seconds}


class _ByteCounter(NoOpCallback):
    """Report each chunk of a transfer to `count`, and pass progress on to `inner`."""

    def __init__(self, inner: Callback, count: Callable[[int], None]) -> None:
        super().__init__()
        self.inner = inner
        self.count = count

    def set_size(self, size: int | None) -> None:
        self.inner.set_size(size)

    def relative_update(self, inc: int = 1) -> None:
        self.count(inc)
        self.inner.relative_update(inc)


@dataclass
class _BusyTimer:
    """Add up the time that one or more activities overlap, so each second counts once."""

    total: float = 0.0
    active: int = 0
    since: float = 0.0

    def start(self) -> None:
        if not self.active:
            self.since = time.perf_counter()
        self.active += 1

    def stop(self) -> None:
        self.active -= 1
        if not self.active:
            self.total += time.perf_counter() - self.since

    def seconds(self) -> float:
        return self.total + (time.perf_counter() - self.since if self.active else 0.0)


@dataclass(eq=False)
class _TransferRecorder:
    """
    Collect the counters behind `TransferStats`.

    Requests come from aiohttp traces. Bytes must come from the callers, as aiohttp traces
    received chunks only in `ClientResponse.read()`, and fsspec streams with `content.read()`.

    Busy time runs while one or more operations are active, so that concurrent operations
    do not make the average rate look too low.
    """

    requests: int = 0
    failed_requests: int = 0
    operations: int = 0
    failed_operations: int = 0
    bytes_read: int = 0
    bytes_written: int = 0
    busy: _BusyTimer = field(default_factory=_BusyTimer)
    responses: Counter[tuple[str, str, int | ResponseFailure]] = field(default_factory=Counter)
    cache_busy: defaultdict[str, _BusyTimer] = field(default_factory=lambda: defaultdict(_BusyTimer))
    cache_bytes_read: Counter[str] = field(default_factory=Counter)
    trace_config: aiohttp.TraceConfig = field(default_factory=aiohttp.TraceConfig, init=False, repr=False)
    # Sync reads record from the caller's thread, and the rest from the event loop.
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.trace_config.on_request_start.append(self._on_request_start)
        self.trace_config.on_request_redirect.append(self._on_request_redirect)
        self.trace_config.on_connection_create_start.append(self._on_connection)
        self.trace_config.on_connection_reuseconn.append(self._on_connection)
        self.trace_config.on_request_end.append(self._on_request_end)
        self.trace_config.on_request_exception.append(self._on_request_exception)

    async def _on_request_start(self, _session: aiohttp.ClientSession, context: SimpleNamespace, _params: aiohttp.TraceRequestStartParams) -> None:
        context.redirected = False
        with self._lock:
            self.requests += 1

    async def _on_request_redirect(self, _session: aiohttp.ClientSession, context: SimpleNamespace, params: aiohttp.TraceRequestRedirectParams) -> None:
        # With no target, aiohttp stops here and on_request_end reports this response.
        headers = params.response.headers
        if aiohttp.hdrs.LOCATION not in headers and aiohttp.hdrs.URI not in headers:
            return
        self._add_response(params.response.url, params.method, params.response.status)
        context.redirected = True

    async def _on_connection(self, _session: aiohttp.ClientSession, context: SimpleNamespace, _params: object) -> None:
        # aiohttp sends no on_request_start for a redirect's next hop, and it can stop
        # before that hop. Only a request about to go out needs a connection.
        if context.redirected:
            context.redirected = False
            with self._lock:
                self.requests += 1

    async def _on_request_end(self, _session: aiohttp.ClientSession, _context: SimpleNamespace, params: aiohttp.TraceRequestEndParams) -> None:
        self._add_response(params.response.url, params.method, params.response.status)
        if params.response.status >= 400 and params.response.status != 404:
            self._add_failed_request()

    async def _on_request_exception(self, _session: aiohttp.ClientSession, context: SimpleNamespace, params: aiohttp.TraceRequestExceptionParams) -> None:
        # A cancelled request is not a failure.
        if isinstance(params.exception, Exception):
            # A redirect that stopped here already has its response.
            if not context.redirected:
                self._add_response(params.url, params.method, ResponseFailure.NO_RESPONSE)
            self._add_failed_request()

    def _add_response(self, url: object, method: str, status: int | ResponseFailure) -> None:
        with self._lock:
            self.responses[_host_of(url), method, status] += 1

    def _add_failed_request(self) -> None:
        with self._lock:
            self.failed_requests += 1

    def add_body_failure(self, url: str) -> None:
        # The caller does not know the method. This assumes GET, the data read, so the rare
        # broken PROPFIND body from info() shows as GET.
        self._add_response(url, "GET", ResponseFailure.BODY)

    def add_read(self, size: int, cache: str) -> None:
        with self._lock:
            self.bytes_read += size
            self.cache_bytes_read[cache] += size

    def add_written(self, size: int) -> None:
        with self._lock:
            self.bytes_written += size

    @contextmanager
    def operation(self) -> Iterator[None]:
        """Count an operation, and add its time to the busy time."""
        with self._lock:
            self.operations += 1
            self.busy.start()
        try:
            yield
        except FileNotFoundError:
            raise
        except Exception:
            with self._lock:
                self.failed_operations += 1
            raise
        finally:
            with self._lock:
                self.busy.stop()

    @asynccontextmanager
    async def async_operation(self) -> AsyncIterator[None]:
        """`operation` for use as a decorator on a coroutine, where a sync one exits too early."""
        with self.operation():
            yield

    @contextmanager
    def cache_active(self, cache: str) -> Iterator[None]:
        """Add the time of one attempt on `cache` to its busy time."""
        with self._lock:
            timer = self.cache_busy[cache]
            timer.start()
        try:
            yield
        finally:
            with self._lock:
                timer.stop()

    def snapshot(self) -> TransferStats:
        with self._lock:
            return TransferStats(
                requests=self.requests,
                failed_requests=self.failed_requests,
                operations=self.operations,
                failed_operations=self.failed_operations,
                bytes_read=self.bytes_read,
                bytes_written=self.bytes_written,
                busy_seconds=self.busy.seconds(),
                responses=Counter(self.responses),
                cache_busy_seconds={cache: timer.seconds() for cache, timer in self.cache_busy.items()},
                cache_bytes_read=Counter(self.cache_bytes_read),
            )
