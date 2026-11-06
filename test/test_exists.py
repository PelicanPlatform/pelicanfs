"""
Copyright (C) 2025, Pelican Project, Morgridge Institute for Research

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
import gc
from contextlib import contextmanager
from unittest.mock import AsyncMock

import aiohttp.connector
import pytest
from pytest_httpserver import HTTPServer

import pelicanfs.core
from pelicanfs.exceptions import NoAvailableSource, NoCredentialsException


def test_exists_nonexistent_object(httpserver: HTTPServer, get_client, get_webdav_client):
    """
    Test that exists() returns False when cache returns 404 for a non-existent object.

    This tests the fix in get_working_cache() where 404 responses from caches
    are now accepted as valid (indicating the cache is working, but the object
    doesn't exist), rather than causing the cache to be marked as bad.
    """
    foo_bar_url = httpserver.url_for("/foo/bar")

    # Mock the pelican configuration endpoint
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    # Mock the director response with cache information
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )

    # Mock cache HEAD request returning 404 during cache selection
    # This is the key test: get_working_cache should accept 404 as valid
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data(
        "",
        status=404,
    )

    # Mock the PROPFIND request used by _exists via WebDAV client
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data(
        "",
        status=404,
    )

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        get_webdav_client=get_webdav_client,
        skip_instance_cache=True,
    )

    # The key assertion: exists() should return False, not raise an exception
    assert pelfs.exists("/foo/bar") is False


@pytest.mark.parametrize(
    ("body", "status"),
    [
        pytest.param("hello, world!", 200, id="object"),
        # A cache refuses to describe a collection, which is not a denial that it exists
        pytest.param("Unable to locate /foo/bar; is a directory", 409, id="collection-refused-by-cache"),
    ],
)
def test_exists_existing_object(httpserver: HTTPServer, get_client, get_webdav_client, body, status):
    """
    Test that exists() returns True when the cache's PROPFIND shows something is there.

    This is a complementary test to ensure the normal case still works correctly.
    """
    foo_bar_url = httpserver.url_for("/foo/bar")

    # Mock the pelican configuration endpoint
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    # Mock the director response with cache information
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )

    # Mock cache HEAD request returning 200 (object exists and cache is working)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data(
        "hello, world!",
        status=200,
    )

    # Mock the PROPFIND request used by _exists via WebDAV client
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data(body, status=status)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        get_webdav_client=get_webdav_client,
        skip_instance_cache=True,
    )

    # exists() should return True for an existing object
    assert pelfs.exists("/foo/bar") is True


@contextmanager
def connections_finalised_for(server):
    """
    Watch the connections aiohttp finalises for "server" while the block runs.

    Yields (finalised, leaked): every connection aiohttp let go of, and the subset that
    still held a protocol at that moment - which is the state aiohttp itself reports as
    "Unclosed connection". Hooking Connection.__del__ is the only way to observe the leak
    from outside, and the count is only complete once a collection has run, so the block
    collects on the way in and on the way out.

    Both lists are filtered to "server". Unrelated connections belonging to other tests do
    get finalised inside this window - the gc.collect() below reaches whatever happens to
    be garbage - and counting those would fail this test for someone else's leak.

    Connection.__del__, _key and _protocol are aiohttp internals, so callers assert that
    "finalised" is non-empty as well as that "leaked" is empty: if any of those move, the
    hook stops recording and an assertion on "leaked" alone would pass without having
    observed anything at all.
    """
    finalised, leaked = [], []
    original_del = aiohttp.connector.Connection.__del__

    def counting_del(self, *args, **kwargs):
        key = self._key
        if (key.host, key.port) == (server.host, server.port):
            finalised.append(key)
            if self._protocol is not None:
                leaked.append(key)
        original_del(self, *args, **kwargs)

    aiohttp.connector.Connection.__del__ = counting_del
    gc.collect()
    try:
        yield finalised, leaked
    finally:
        gc.collect()
        aiohttp.connector.Connection.__del__ = original_del


def connections_held_by(client):
    """
    How many connections "client" currently has checked out of its pool.

    connector._acquired is an aiohttp internal, but it is the only view of the property
    that matters here: a response that has not been released keeps its connection checked
    out, so this stays at zero only if each call really does release as it ends.
    """
    return len(client.session.connector._acquired)


# aiohttp finishes a small body along with the headers and releases the connection by
# itself, which hides the leak for reasons that have nothing to do with the code under
# test. Measured against aiohttp 3.12: under roughly 20 KB nothing ever looks leaked, and
# between 20 KB and 100 KB it is intermittent. Each entry below is about 190 bytes, so
# this is ~1.5 MB - far enough clear of that band to be reliable, and cheap to build.
_ENTRIES_TOO_LARGE_TO_ARRIVE_WITH_HEADERS = 8000


def large_multistatus_response():
    """A PROPFIND body too large for aiohttp to have finished reading with the headers."""
    entries = "".join(
        f"<d:response><d:href>/foo/bar/{i}</d:href><d:propstat><d:prop>"
        f"<d:getcontentlength>13</d:getcontentlength><d:resourcetype/></d:prop>"
        f"<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        for i in range(_ENTRIES_TOO_LARGE_TO_ARRIVE_WITH_HEADERS)
    )
    return f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">{entries}</d:multistatus>'


def expect_propfind(httpserver, status):
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data(
        large_multistatus_response(),
        status=status,
        content_type="application/xml",
    )


def webdav_options(httpserver, httpclient_ssl_context):
    return {
        "hostname": httpserver.url_for("/"),
        "token": "test-token",
        "verify_ssl": httpclient_ssl_context,
    }


async def check_through_webdav_client(httpserver, httpclient_ssl_context):
    async with pelicanfs.core.get_webdav_client(webdav_options(httpserver, httpclient_ssl_context)) as client:
        return await client.exists("/foo/bar")


def test_webdav_client_releases_the_response(httpserver: HTTPServer, httpclient_ssl_context, run_async):
    """
    The client's exists(), which backs exists(), only looks at the status, and must still
    release the response it never reads.
    """
    expect_propfind(httpserver, status=207)

    with connections_finalised_for(httpserver) as (finalised, leaked):
        assert run_async(check_through_webdav_client(httpserver, httpclient_ssl_context)) is True

    assert finalised, "no connection to the test server was finalised, so this observed nothing"
    assert leaked == [], "the response was not released, so its connection leaked"


def test_webdav_client_releases_the_response_on_an_error_status(httpserver: HTTPServer, httpclient_ssl_context, run_async):
    """
    A response that raises for its error status must be released too. This is the common
    case for exists(), which returns False by way of the 404's exception.
    """
    expect_propfind(httpserver, status=404)

    with connections_finalised_for(httpserver) as (finalised, leaked):
        assert run_async(check_through_webdav_client(httpserver, httpclient_ssl_context)) is False

    assert finalised, "no connection to the test server was finalised, so this observed nothing"
    assert leaked == [], "the response was not released, so its connection leaked"


def test_webdav_client_does_not_hoard_connections_across_calls(threaded_httpserver: HTTPServer, httpclient_ssl_context, run_async):
    """
    A reused client must not accumulate connections: each call gives its connection back
    before it returns.

    Nothing bounds how many calls a client serves or how long it is kept, so releasing
    only when it closes would keep every connection checked out and every body resident
    until then. That would not show up in the tests above, which close the client before
    looking at anything.

    This one needs the threaded server: held-open connections are exactly what it is
    looking for, and the single-threaded one stops serving while any are held.
    """
    threaded_httpserver.clear()
    expect_propfind(threaded_httpserver, status=207)

    async def held_after_each_of_several_calls():
        held = []
        async with pelicanfs.core.get_webdav_client(webdav_options(threaded_httpserver, httpclient_ssl_context)) as client:
            for _ in range(3):
                assert await client.exists("/foo/bar") is True
                held.append(connections_held_by(client))
        return held

    held = run_async(held_after_each_of_several_calls())
    assert held == [0, 0, 0], f"a call returned still holding its connection: {held}"


@pytest.mark.parametrize(
    ("path", "discovery_url"),
    [
        pytest.param("s3://bucket/key", "", id="foreign-scheme"),
        pytest.param("localhost:63371/ns/obj", "", id="host-port-without-scheme"),
        pytest.param("pelican://[broken/path", "", id="malformed-url"),
        pytest.param("/ns/obj", "", id="absolute-path-without-federation"),
        pytest.param("pelican://other.example.com/ns/obj", "pelican://fed.example.com/", id="other-federation"),
    ],
)
@pytest.mark.parametrize("call", ["positional", "keyword", "async"])
def test_exists_is_false_for_unusable_path(path, discovery_url, call, monkeypatch):
    is_async = call == "async"
    fs = pelicanfs.core.PelicanFileSystem(discovery_url, asynchronous=is_async, skip_instance_cache=True)
    cache = AsyncMock(side_effect=AssertionError("an unusable path must not reach the network"))
    monkeypatch.setattr(fs, "get_working_cache", cache)

    if is_async:
        result = asyncio.run(fs._exists(path))
    elif call == "keyword":
        result = fs.exists(path=path)
    else:
        result = fs.exists(path)

    assert result is False
    cache.assert_not_awaited()


@pytest.mark.parametrize("error", [NoCredentialsException("no token"), NoAvailableSource("no cache")])
def test_exists_reports_failures_for_usable_path(error, monkeypatch):
    fs = pelicanfs.core.PelicanFileSystem("pelican://fed.example.com/", skip_instance_cache=True)
    monkeypatch.setattr(fs, "get_working_cache", AsyncMock(side_effect=error))

    with pytest.raises(type(error)):
        fs.exists("/ns/obj")
