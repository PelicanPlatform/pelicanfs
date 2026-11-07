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

Tests that assert which Authorization header reaches the server.
"""

import gc
import weakref
from datetime import UTC, datetime, timedelta

import aiohttp
import pytest
from fsspec.asyn import AsyncFileSystem, get_loop, sync
from fsspec.implementations.http import HTTPFileSystem
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Response

from pelicanfs.core import PelicanFileSystem
from pelicanfs.token_generator import TokenGenerator, TokenInfo

GENERATED = "Bearer gen-token"


def requests_to(server: HTTPServer, method: str):
    return [request for request, _ in server.log if request.method == method]


def _short_body(_request):
    """A 200 whose body is shorter than its Content-Length, so reading it fails."""
    response = Response(iter([b"he"]), status=200, direct_passthrough=True)
    response.headers["Content-Length"] = "5"
    return response


@pytest.fixture(name="generated_token")
def fixture_generated_token(monkeypatch):
    def get_token(self):
        self.token = TokenInfo("gen-token", datetime.now(UTC) + timedelta(hours=1))
        return "gen-token"

    monkeypatch.setattr(TokenGenerator, "get_token", get_token)


@pytest.fixture(name="federation")
def fixture_federation(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """
    A require-token namespace with the director on `httpserver` and the cache on
    `httpserver2`. Returns a factory for filesystems that use this federation.
    """
    cache_url = httpserver2.url_for("/foo/bar")

    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{cache_url}>; rel="duplicate"; pri=1; depth=1',
            "Location": cache_url,
            "X-Pelican-Namespace": "namespace=/foo, require-token=true",
        },
    )
    httpserver2.expect_request("/foo/bar").respond_with_data("hello, world!")

    def make_fs(**kwargs):
        return PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, **kwargs)

    return make_fs


def _sync_read(fs, **kwargs):
    with fs.open("/foo/bar", "rb", **kwargs) as fp:
        return fp.read()


def _async_read(fs):
    async def read():
        fp = await fs.open_async("/foo/bar")
        try:
            return await fp.read()
        finally:
            await fp.close()

    return sync(fs.loop, read)


@pytest.mark.usefixtures("generated_token")
@pytest.mark.parametrize(
    "read",
    [
        pytest.param(lambda fs: _sync_read(fs, cache_type="none"), id="range"),
        pytest.param(lambda fs: _sync_read(fs, block_size=0), id="stream"),
        pytest.param(_async_read, id="async"),
    ],
)
def test_read_retry_sends_token_to_replacement_cache(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, read):
    """A cache first seen by a read retry is prepared for the token before the retry."""
    urls = [server.url_for("/foo/bar") for server in (httpserver, httpserver2)]
    link = ", ".join(f'<{url}>; rel="duplicate"; pri={i}; depth=1' for i, url in enumerate(urls, 1))
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": link, "Location": urls[0], "X-Pelican-Namespace": "namespace=/foo, require-token=true"},
    )
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    # The stream file starts its GET while opening, so open succeeds and the first read fails
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_handler(_short_body)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=500)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_data("hello")

    fs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    assert read(fs) == b"hello"
    assert [request.headers.get("Authorization") for request in requests_to(httpserver2, "GET")] == [GENERATED]


def _refused(request, fs, *, expired):
    """A cache's 403. With expired, the token runs out while the request is in flight."""
    if expired:
        fs._token_expiry = datetime.now(UTC) - timedelta(minutes=1)
    return Response(status=403)


@pytest.fixture(name="numbered_tokens")
def fixture_numbered_tokens(monkeypatch):
    tokens = iter(["token-1", "token-2"])

    def get_token(self):
        token = next(tokens)
        self.token = TokenInfo(token, datetime.now(UTC) + timedelta(hours=1))
        return token

    monkeypatch.setattr(TokenGenerator, "get_token", get_token)


@pytest.mark.usefixtures("numbered_tokens")
def test_expired_token_retries_on_same_cache(federation, httpserver2):
    """A 403 for an expired token gets a new token and does not evict the cache."""
    fs = federation()
    httpserver2.expect_oneshot_request("/foo/bar", method="GET").respond_with_handler(lambda request: _refused(request, fs, expired=True))

    assert fs.cat("/foo/bar") == b"hello, world!"

    assert [request.headers["Authorization"] for request in requests_to(httpserver2, "GET")] == ["Bearer token-1", "Bearer token-2"]
    assert fs._get_prefix_info("/foo/bar").cache_manager._cache_list == [httpserver2.url_for("").rstrip("/")]


@pytest.mark.usefixtures("numbered_tokens")
def test_valid_token_refused_is_not_retried(federation, httpserver2):
    """A 403 for a token that has not expired will not get better on any cache."""
    fs = federation()
    httpserver2.expect_oneshot_request("/foo/bar", method="GET").respond_with_handler(lambda request: _refused(request, fs, expired=False))

    with pytest.raises(aiohttp.ClientResponseError, match="403"):
        fs.cat("/foo/bar")

    assert len(requests_to(httpserver2, "GET")) == 1
    assert fs._get_prefix_info("/foo/bar").cache_manager._cache_list == [httpserver2.url_for("").rstrip("/")]


@pytest.mark.usefixtures("numbered_tokens")
def test_expired_token_retries_info_on_the_origin(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, get_webdav_client):
    """info() asks the collections endpoint again with a new token after a 403 for an expired one, as reads do."""
    origin_url = httpserver2.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{origin_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": f"namespace=/foo, require-token=true, collections-url={httpserver2.url_for('/')}",
        },
    )
    fs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, get_webdav_client=get_webdav_client, skip_instance_cache=True)
    multistatus = (
        '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:"><D:response><D:href>/foo/bar</D:href>'
        "<D:propstat><D:prop><D:getcontentlength>13</D:getcontentlength></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
        "</D:response></D:multistatus>"
    )
    httpserver2.expect_oneshot_request("/foo/bar", method="PROPFIND").respond_with_handler(lambda request: _refused(request, fs, expired=True))
    httpserver2.expect_request("/foo/bar", method="PROPFIND").respond_with_data(multistatus, status=207, content_type="application/xml")

    assert fs.info("/foo/bar")["size"] == 13

    assert [request.headers["Authorization"] for request in requests_to(httpserver2, "PROPFIND")] == ["Bearer token-1", "Bearer token-2"]


@pytest.mark.usefixtures("generated_token")
def test_open_async_read_sends_token(federation, httpserver2):
    async def read():
        fp = await federation(asynchronous=True).open_async("/foo/bar")
        try:
            return await fp.read()
        finally:
            await fp.close()

    # On fsspec's loop, so the get_client fixture can close the session on its own loop
    assert sync(get_loop(), read) == b"hello, world!"
    assert [request.headers.get("Authorization") for request in requests_to(httpserver2, "GET")] == [GENERATED]


@pytest.mark.usefixtures("generated_token")
def test_per_call_headers_keep_token(federation, httpserver2):
    assert federation().cat_file("/foo/bar", headers={"X-Foo": "1"}) == b"hello, world!"

    request = requests_to(httpserver2, "GET")[-1]
    assert request.headers.get("Authorization") == GENERATED
    assert request.headers.get("X-Foo") == "1"


@pytest.mark.usefixtures("generated_token")
def test_per_call_authorization_wins(federation, httpserver2):
    federation().cat_file("/foo/bar", headers={"authorization": "Bearer mine"})

    assert requests_to(httpserver2, "GET")[-1].headers.get("Authorization") == "Bearer mine"


@pytest.mark.usefixtures("generated_token")
def test_open_with_per_call_headers(federation, httpserver2):
    with federation().open("/foo/bar", "rb", headers={"X-Foo": "1"}) as fp:
        assert fp.read() == b"hello, world!"

    request = requests_to(httpserver2, "GET")[-1]
    assert request.headers.get("Authorization") == GENERATED
    assert request.headers.get("X-Foo") == "1"


@pytest.mark.skipif(HTTPFileSystem._pipe_file is AsyncFileSystem._pipe_file, reason="HTTP pipe_file needs fsspec>=2025.3.0")
@pytest.mark.usefixtures("generated_token")
def test_pipe_file_after_read_sends_token(federation, httpserver2):
    pelfs = federation()
    pelfs.cat_file("/foo/bar")

    pelfs.pipe_file(httpserver2.url_for("/foo/bar"), b"x")

    assert requests_to(httpserver2, "PUT")[-1].headers.get("Authorization") == GENERATED


@pytest.mark.usefixtures("generated_token")
def test_generated_token_stays_out_of_caller_headers(federation):
    headers = {"X-Foo": "1"}
    pelfs = federation(headers=headers)

    pelfs.cat_file("/foo/bar")

    assert headers == {"X-Foo": "1"}
    assert "gen-token" not in repr(pelfs.storage_options)


@pytest.mark.usefixtures("generated_token")
def test_failed_regeneration_sends_no_stale_token(federation, httpserver2, monkeypatch):
    pelfs = federation()
    pelfs.cat_file("/foo/bar")

    def fail(_self):
        msg = "no token"
        raise RuntimeError(msg)

    monkeypatch.setattr(TokenGenerator, "get_token", fail)
    pelfs._token_expiry = datetime.now(UTC) - timedelta(hours=1)
    sent = len(requests_to(httpserver2, "GET"))

    with pytest.raises(RuntimeError, match="no token"):
        pelfs.cat_file("/foo/bar")

    assert len(requests_to(httpserver2, "GET")) == sent


@pytest.mark.usefixtures("generated_token")
def test_lowercase_caller_authorization_is_the_token(federation, httpserver2):
    pelfs = federation(headers={"authorization": "Bearer mine"})

    pelfs.cat_file("/foo/bar")

    assert pelfs.token == "Bearer mine"
    assert requests_to(httpserver2, "GET")[-1].headers.getlist("Authorization") == ["Bearer mine"]


@pytest.mark.usefixtures("generated_token")
def test_director_never_receives_token(federation, httpserver, httpserver2):
    pelfs = federation()
    pelfs.cat_file("/foo/bar")
    with pelfs._namespace_lock:
        pelfs._namespace_cache.clear()
    pelfs.cat_file("/foo/bar")

    assert requests_to(httpserver2, "GET")
    assert [request.headers.get("Authorization") for request, _ in httpserver.log] == [None] * len(httpserver.log)


@pytest.mark.usefixtures("generated_token")
def test_session_does_not_keep_filesystem_alive(federation):
    pelfs = federation()
    pelfs.cat_file("/foo/bar")
    session = sync(pelfs.loop, pelfs.http_file_system.set_session)
    ref = weakref.ref(pelfs)

    del pelfs
    gc.collect()

    assert ref() is None
    assert session.closed


def _two_cache_token_federation(httpserver, httpserver2):
    urls = [server.url_for("/foo/bar") for server in (httpserver, httpserver2)]
    link = ", ".join(f'<{url}>; rel="duplicate"; pri={i}; depth=1' for i, url in enumerate(urls, 1))
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": link, "Location": urls[0], "X-Pelican-Namespace": "namespace=/foo, require-token=true"},
    )
    return [url.removesuffix("/foo/bar") for url in urls]


@pytest.mark.usefixtures("generated_token")
@pytest.mark.parametrize("status", [401, 403])
def test_probe_moves_past_cache_that_refuses_token(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, status):
    """A cache that refuses a valid token must not stay first while another works."""
    hosts = _two_cache_token_federation(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=status)
    httpserver2.expect_request("/foo/bar").respond_with_data("hello")

    fs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    assert fs.cat("/foo/bar") == b"hello"
    assert fs._get_prefix_info("/foo/bar").cache_manager._cache_list == hosts[::-1]


@pytest.mark.usefixtures("generated_token")
def test_probe_token_refused_everywhere_reports_auth_error(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """When every cache refuses the token, the caller sees the auth error, not NoAvailableSource."""
    hosts = _two_cache_token_federation(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar").respond_with_data("", status=403)
    httpserver2.expect_request("/foo/bar").respond_with_data("", status=403)

    fs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pytest.raises(aiohttp.ClientResponseError, match="403"):
        fs.cat("/foo/bar")
    assert fs._get_prefix_info("/foo/bar").cache_manager._cache_list == hosts
