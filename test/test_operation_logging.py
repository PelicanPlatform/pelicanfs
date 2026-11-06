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

import asyncio
import logging
import re

import aiohttp
import pytest
from fsspec.asyn import get_loop, sync
from pytest_httpserver import HTTPServer

from pelicanfs.core import PelicanFileSystem
from pelicanfs.log_utils import log_operation

OP = r"\[([0-9a-f]{6}(?:\.\d+)*)\]"


def serve_federation(server: HTTPServer, *paths: str) -> None:
    server.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": server.url_for("/")})
    for path in paths:
        url = server.url_for(path)
        server.expect_oneshot_request(path, method="GET").respond_with_data(
            "",
            status=307,
            headers={"Link": f'<{url}>; rel="duplicate"; pri=1; depth=1', "Location": url, "X-Pelican-Namespace": "namespace=/foo"},
        )
        server.expect_request(path, method="HEAD").respond_with_data("", status=200)
        server.expect_request(path, method="GET").respond_with_data("ok")


@pytest.fixture(name="pelfs")
def fixture_pelfs(httpserver: HTTPServer, get_client):
    return PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)


def pelican_messages(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name.startswith("fsspec")]


def operation_ids(messages: list[str]) -> set[str]:
    return {match[1] for message in messages if (match := re.match(OP, message))}


def test_requests_are_logged_with_status_and_duration(httpserver: HTTPServer, pelfs, caplog):
    serve_federation(httpserver, "/foo/bar")

    with caplog.at_level(logging.DEBUG, logger="fsspec"):
        assert pelfs.cat("/foo/bar?authz=SECRET") == b"ok"

    messages = pelican_messages(caplog)
    url = re.escape(httpserver.url_for("/foo/bar"))
    for pattern in (rf"HTTP GET {url}\?authz=<redacted> -> 307 in \d+\.\d{{3}}s", rf"HTTP HEAD {url}\?authz=<redacted> -> 200 in \d+\.\d{{3}}s"):
        assert any(re.search(pattern, message) for message in messages)
    assert not any("SECRET" in message for message in messages)


def test_request_failure_is_logged(httpserver: HTTPServer, pelfs, caplog):
    url = "https://127.0.0.1:1/foo/bar"
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{url}>; rel="duplicate"; pri=1; depth=1', "Location": url, "X-Pelican-Namespace": "namespace=/foo"},
    )

    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"), pytest.raises(Exception, match="No working cache"):
        sync(pelfs.loop, pelfs.get_working_cache, "/foo/bar")

    assert any(re.search(rf"HTTP HEAD {re.escape(url)} failed after \d+\.\d{{3}}s: ", message) for message in pelican_messages(caplog))


def test_request_failure_log_omits_authorization_header(httpserver: HTTPServer, pelfs, caplog):
    url = httpserver.url_for("/loop")
    httpserver.expect_request("/loop").respond_with_data("", status=302, headers={"Location": url})

    async def fetch() -> None:
        session = await pelfs.http_file_system.set_session()
        async with session.get(url, headers={"Authorization": "Bearer SECRET"}):
            pass

    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"), pytest.raises(aiohttp.TooManyRedirects):
        sync(pelfs.loop, fetch)

    messages = pelican_messages(caplog)
    assert not any("SECRET" in message for message in messages)
    assert any(re.search(rf"HTTP GET {re.escape(url)} failed after \d+\.\d{{3}}s: TooManyRedirects: ", message) for message in messages)


def test_redirect_is_logged(httpserver: HTTPServer, pelfs, caplog):
    target = httpserver.url_for("/moved")
    httpserver.expect_request("/old").respond_with_data("", status=302, headers={"Location": target})
    httpserver.expect_request("/moved").respond_with_data("ok")

    async def fetch() -> None:
        session = await pelfs.http_file_system.set_session()
        async with session.get(httpserver.url_for("/old")) as resp:
            await resp.read()

    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"):
        sync(pelfs.loop, fetch)

    assert any(f"HTTP GET {httpserver.url_for('/old')} -> 302 to {target}" in message for message in pelican_messages(caplog))


async def _session_and_listing_requests_are_logged(get_webdav_client, url: str) -> None:
    async with get_webdav_client({"hostname": url, "token": "tok"}) as client:
        await client.check("/foo")


def test_webdav_requests_are_logged(httpserver: HTTPServer, get_webdav_client, caplog):
    httpserver.expect_request("/foo", method="PROPFIND").respond_with_data("", status=207)

    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"):
        # This loop stays open, so the TLS connection can finish closing.
        sync(get_loop(), _session_and_listing_requests_are_logged, get_webdav_client, httpserver.url_for("/"))

    assert any("HTTP PROPFIND" in message and "-> 207 in " in message for message in pelican_messages(caplog))


def test_all_lines_of_an_operation_share_its_id(httpserver: HTTPServer, pelfs, caplog):
    serve_federation(httpserver, "/foo/bar")

    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"):
        assert sync(pelfs.loop, pelfs._cat_file, "/foo/bar") == b"ok"

    messages = pelican_messages(caplog)
    assert len(operation_ids(messages)) == 1
    assert all(re.match(OP, message) for message in messages)
    assert re.search(r"Start _cat_file /foo/bar$", messages[0])
    assert re.search(r"_cat_file finished in \d+\.\d{3}s$", messages[-1])


def test_bulk_operation_gives_each_object_a_child_id(httpserver: HTTPServer, pelfs, caplog):
    serve_federation(httpserver, "/foo/a", "/foo/b")

    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"):
        assert pelfs.cat(["/foo/a", "/foo/b"]) == {"/foo/a": b"ok", "/foo/b": b"ok"}

    messages = pelican_messages(caplog)
    start = next(message for message in messages if "Start _cat " in message)
    assert start.endswith("2 paths")
    root = re.match(OP, start)[1]
    children = {message.split("]")[0] for message in messages if "Start _cat_file" in message}
    assert children == {f"[{root}.1", f"[{root}.2"}


def test_concurrent_operations_have_their_own_ids(httpserver: HTTPServer, pelfs, caplog):
    serve_federation(httpserver, "/foo/a", "/foo/b")

    async def both() -> list[bytes]:
        return await asyncio.gather(pelfs._cat_file("/foo/a"), pelfs._cat_file("/foo/b"))

    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"):
        sync(pelfs.loop, both)

    assert len(operation_ids(pelican_messages(caplog))) == 2


def test_failed_operation_is_logged(httpserver: HTTPServer, pelfs, caplog):
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_data("", status=503)

    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"), pytest.raises(Exception, match="503"):
        sync(pelfs.loop, pelfs._cat_file, "/foo/bar")

    assert re.search(r"_cat_file failed after \d+\.\d{3}s: InvalidMetadata", pelican_messages(caplog)[-1])


@pytest.mark.parametrize("async_open", [False, True])
def test_open_resolves_within_an_operation(httpserver: HTTPServer, pelfs, caplog, async_open):
    serve_federation(httpserver, "/foo/bar")

    async def open_and_close() -> None:
        file = await pelfs.open_async("/foo/bar")
        await file.close()

    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"):
        if async_open:
            sync(pelfs.loop, open_and_close)
        else:
            pelfs.open("/foo/bar", "rb").close()

    messages = pelican_messages(caplog)
    choosing = next(message for message in messages if "Choosing a cache" in message)
    assert re.match(OP, choosing)
    assert any("Start open" in message for message in messages)


def test_log_operation_scope(caplog):
    logger = logging.getLogger("fsspec.pelican")
    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"):
        with log_operation("outer"), log_operation("inner"):
            logger.debug("inside")
        logger.debug("outside")

    messages = pelican_messages(caplog)
    root = re.match(OP, messages[0])[1]
    assert messages[0] == f"[{root}] Start outer "
    assert next(message for message in messages if message.endswith("inside")) == f"[{root}.1] inside"
    assert messages[-1] == "outside"
