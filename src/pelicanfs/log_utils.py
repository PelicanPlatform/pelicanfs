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

import contextvars
import itertools
import logging
import re
import secrets
import time
from collections.abc import Iterator, Sized
from contextlib import contextmanager
from typing import Any

import aiohttp
from multidict import CIMultiDictProxy

# Finer than DEBUG for internal call-flow tracing (not enabled by default).
TRACE = 5

logger = logging.getLogger("fsspec.pelican")

_QUERY_VALUE = re.compile(r"""([?&][^=&?\s'"<>]*=)[^&\s'"<>]+""")


def trace(logger: logging.Logger, msg: str, *args: object, **kwargs: Any) -> None:
    logger.log(TRACE, msg, *args, stacklevel=2, **kwargs)


class TokenForLog:
    """Show a JWT as header and payload (no signature), and hide any other value.

    Formatting runs only when a log record is emitted.
    """

    def __init__(self, token: str | None) -> None:
        self.token = token

    def __str__(self) -> str:
        if not self.token:
            return "None"
        raw = self.token.removeprefix("Bearer ").strip()
        parts = raw.split(".")
        # A JWT header is base64url JSON, so it starts with the encoding of '{"'.
        if len(parts) == 3 and raw.startswith("eyJ"):
            return f"{parts[0]}.{parts[1]}"
        return f"<non-jwt len={len(raw)}>"


def redact_query(text: str) -> str:
    """Hide the value of each URL query parameter, which can be an authz token."""
    return _QUERY_VALUE.sub(r"\1<redacted>", text)


def _redact_if_str(value: object) -> object:
    return redact_query(value) if isinstance(value, str) else value


def redact_exception(error: BaseException) -> None:
    """Hide query values in an exception and its chain, in place, before the caller sees them."""
    current: BaseException | None = error
    while current:
        current.args = tuple(_redact_if_str(arg) for arg in current.args)
        if isinstance(current, aiohttp.ClientResponseError):
            info = current.request_info
            headers = info.headers.copy()
            headers.popall("Authorization", None)
            current.request_info = aiohttp.RequestInfo(info.url.with_query(None), info.method, CIMultiDictProxy(headers), info.real_url.with_query(None))
            # Each response in the history also holds the URL.
            current.history = ()
            current.args = (current.request_info, current.history)
        current = current.__cause__ or current.__context__


class _Operation:
    def __init__(self, op_id: str) -> None:
        self.id = op_id
        self.children = itertools.count(1)


_current_operation: contextvars.ContextVar[_Operation | None] = contextvars.ContextVar("pelicanfs_operation", default=None)


@contextmanager
def log_operation(name: str, target: object = "") -> Iterator[None]:
    """
    Log the start and end of an operation, and put its ID on each log line inside it.

    A nested operation gets a child ID: `a1b2c3.2` is the second child of `a1b2c3`. The ID
    is a context variable, so tasks started inside inherit it, and it does not reach
    fsspec's IO thread from a sync call. Enter it in a coroutine, not around a `sync()`.
    """
    parent = _current_operation.get()
    op_id = f"{parent.id}.{next(parent.children)}" if parent else secrets.token_hex(3)
    token = _current_operation.set(_Operation(op_id))
    described = f"{len(target)} paths" if isinstance(target, Sized) and not isinstance(target, str) else target
    logger.debug("Start %s %s", name, described)
    start = time.perf_counter()
    try:
        yield
    except Exception as e:
        logger.debug("%s failed after %.3fs: %s: %s", name, time.perf_counter() - start, type(e).__name__, e)
        raise
    else:
        logger.debug("%s finished in %.3fs", name, time.perf_counter() - start)
    finally:
        _current_operation.reset(token)


class _PelicanLogFilter(logging.Filter):
    """Redact query values, and put the current operation ID on the line."""

    def filter(self, record: logging.LogRecord) -> bool:
        operation = _current_operation.get()
        prefix = f"[{operation.id}] " if operation else ""
        try:
            record.msg = prefix + redact_query(record.getMessage())
            record.args = None
        except Exception:
            # The handler reports bad format args without raising, and prints them.
            if isinstance(record.args, dict):
                record.args = {key: _redact_if_str(value) for key, value in record.args.items()}
            elif record.args:
                record.args = tuple(_redact_if_str(arg) for arg in record.args)
        if record.exc_info and not record.exc_text:
            record.exc_text = redact_query(logging.Formatter().formatException(record.exc_info))
        return True


# fsspec.http also logs each URL we hand it.
for _name in ("fsspec.pelican", "fsspec.http"):
    logging.getLogger(_name).addFilter(_PelicanLogFilter())
