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

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import aiohttp
from fsspec.callbacks import Callback, NoOpCallback


@dataclass(frozen=True)
class TransferStats:
    """
    Totals for one filesystem object, from its creation to the time of the call.

    A request is one HTTP request, so each cache attempt is a request of its own. An
    operation is one call that moves data: cat_file, get_file, put_file, or read on an
    open file. An operation fails only when no retry could save it. A missing object
    (404) is not a failure of either kind.
    """

    requests: int = 0
    failed_requests: int = 0
    operations: int = 0
    failed_operations: int = 0
    bytes_read: int = 0
    bytes_written: int = 0
    busy_seconds: float = 0.0

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


class _TransferRecorder:
    """
    Collect the counters behind `TransferStats`.

    Requests come from aiohttp traces. Bytes must come from the callers, as aiohttp traces
    received chunks only in `ClientResponse.read()`, and fsspec streams with `content.read()`.

    Busy time runs while one or more operations are active, so that concurrent operations
    do not make the average rate look too low.
    """

    def __init__(self) -> None:
        # Sync reads record from the caller's thread, and the rest from the event loop.
        self._lock = threading.Lock()
        self._requests = 0
        self._failed_requests = 0
        self._operations = 0
        self._failed_operations = 0
        self._bytes_read = 0
        self._bytes_written = 0
        self._busy_seconds = 0.0
        self._active = 0
        self._busy_since = 0.0

        self.trace_config = aiohttp.TraceConfig()
        self.trace_config.on_request_start.append(self._on_request_start)
        self.trace_config.on_request_end.append(self._on_request_end)
        self.trace_config.on_request_exception.append(self._on_request_exception)

    async def _on_request_start(self, _session: aiohttp.ClientSession, _context: SimpleNamespace, _params: aiohttp.TraceRequestStartParams) -> None:
        with self._lock:
            self._requests += 1

    async def _on_request_end(self, _session: aiohttp.ClientSession, _context: SimpleNamespace, params: aiohttp.TraceRequestEndParams) -> None:
        if params.response.status >= 400 and params.response.status != 404:
            self._add_failed_request()

    async def _on_request_exception(self, _session: aiohttp.ClientSession, _context: SimpleNamespace, params: aiohttp.TraceRequestExceptionParams) -> None:
        # A cancelled request is not a failure.
        if isinstance(params.exception, Exception):
            self._add_failed_request()

    def _add_failed_request(self) -> None:
        with self._lock:
            self._failed_requests += 1

    def add_read(self, size: int) -> None:
        with self._lock:
            self._bytes_read += size

    def add_written(self, size: int) -> None:
        with self._lock:
            self._bytes_written += size

    @contextmanager
    def operation(self) -> Iterator[None]:
        """Count an operation, and add its time to the busy time."""
        with self._lock:
            self._operations += 1
            if not self._active:
                self._busy_since = time.perf_counter()
            self._active += 1
        try:
            yield
        except FileNotFoundError:
            raise
        except Exception:
            with self._lock:
                self._failed_operations += 1
            raise
        finally:
            with self._lock:
                self._active -= 1
                if not self._active:
                    self._busy_seconds += time.perf_counter() - self._busy_since

    def snapshot(self) -> TransferStats:
        with self._lock:
            busy = self._busy_seconds
            if self._active:
                busy += time.perf_counter() - self._busy_since
            return TransferStats(
                requests=self._requests,
                failed_requests=self._failed_requests,
                operations=self._operations,
                failed_operations=self._failed_operations,
                bytes_read=self._bytes_read,
                bytes_written=self._bytes_written,
                busy_seconds=busy,
            )
