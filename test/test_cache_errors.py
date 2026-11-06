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

import errno
import gzip
import socketserver
import threading

import aiohttp
import fsspec.implementations.http as fshttp
import pytest
from aiowebdav2.exceptions import (
    AccessDeniedError,
    ConnectionExceptionError,
    NoConnectionError,
    RemoteResourceNotFoundError,
    ResponseErrorCodeError,
    UnauthorizedError,
)
from fsspec.asyn import sync
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Response

from pelicanfs.core import MAX_CACHE_ATTEMPTS, PelicanFileSystem, _cache_failure
from pelicanfs.exceptions import NoAvailableSource


def _setup_director(httpserver, second=None, path="/foo/bar"):
    """
    Register the pelican-configuration and a single director 307 redirect for path.
    httpserver is the first cache; second, if given, is listed as the next cache.
    """
    caches = [httpserver] if second is None else [httpserver, second]
    urls = [server.url_for(path) for server in caches]
    link = ", ".join(f'<{url}>; rel="duplicate"; pri={i}; depth=1' for i, url in enumerate(urls, 1))
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request(path, method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": link, "Location": urls[0], "X-Pelican-Namespace": "namespace=/foo"},
    )
    return urls[0] if second is None else urls


def test_cat_file_404_raises_file_not_found(httpserver: HTTPServer, get_client):
    """Two sequential 404s must both raise FileNotFoundError, not crash with ValueError."""
    _setup_director(httpserver)
    # First 404
    httpserver.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data("", status=404)
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("", status=404)
    # Second 404 — namespace is cached after the first call so no HEAD probe, just GET
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("", status=404)

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    with pytest.raises(FileNotFoundError):
        pelfs.cat("/foo/bar")
    with pytest.raises(FileNotFoundError):
        pelfs.cat("/foo/bar")


def test_404_does_not_evict_cache(httpserver: HTTPServer, get_client):
    """A 404 must not evict the healthy cache from the namespace cache."""
    _setup_director(httpserver)
    httpserver.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data("", status=404)
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("", status=404)

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    with pytest.raises(FileNotFoundError):
        pelfs.cat("/foo/bar")

    namespace_info = pelfs._get_prefix_info("/foo/bar")
    assert namespace_info is not None
    assert len(namespace_info.cache_manager._cache_list) > 0


def test_open_async_404_does_not_evict_cache(httpserver: HTTPServer, get_client):
    """A 404 surfacing at read() time via open_async must not evict the cache."""
    _setup_director(httpserver)
    # get_working_cache probes with HEAD, then open_async probes with HEAD again for info()
    # both return 404 since the file doesn't exist; two GETs follow (info + read)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=404)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=404)

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    async def _read():
        fp = await pelfs.open_async("/foo/bar")
        try:
            return await fp.read()
        finally:
            await fp.close()

    with pytest.raises(FileNotFoundError):
        sync(pelfs.loop, _read)

    namespace_info = pelfs._get_prefix_info("/foo/bar")
    assert namespace_info is not None
    assert len(namespace_info.cache_manager._cache_list) > 0


def test_double_cache_error_does_not_crash(httpserver: HTTPServer, get_client):
    """Two sequential non-404 failures must not raise ValueError from bad_cache."""
    _setup_director(httpserver)
    # Both calls return 500, which should mark the cache bad without crashing
    httpserver.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data("", status=500)
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("", status=500)
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("", status=500)

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    with pytest.raises(NoAvailableSource):
        pelfs.cat("/foo/bar")
    # Second call must not crash with ValueError even though the cache is already evicted
    with pytest.raises(NoAvailableSource):
        pelfs.cat("/foo/bar")


def _serve_range(data: bytes):
    """Handler that honours a single bytes=start-end Range header with a 206."""

    def handler(request):
        rng = request.headers.get("Range")
        if not rng:
            return Response(data)
        start, end = rng.removeprefix("bytes=").split("-")
        start = int(start)
        end = int(end) + 1 if end else len(data)
        return Response(data[start:end], status=206, headers={"Content-Range": f"bytes {start}-{end - 1}/{len(data)}"})

    return handler


def _broken_body(request):
    """A 200 whose body is shorter than its Content-Length, so the client fails on its first read."""
    response = Response(iter([b"he"]), status=200, direct_passthrough=True)
    response.headers["Content-Length"] = "5"
    return response


def _server_error(_request):
    return Response("", status=500)


def _empty_broken_body(_request):
    """A 200 that ends before its first byte, so a stream fails while it can still fail over."""
    response = Response(iter([b""]), status=200, direct_passthrough=True)
    response.headers["Content-Length"] = "5"
    return response


def _assert_first_cache_evicted(pelfs, httpserver, httpserver2):
    namespace_info = pelfs._get_prefix_info("/foo/bar")
    assert namespace_info is not None
    hosts = namespace_info.cache_manager._cache_list
    assert httpserver.url_for("").rstrip("/") not in hosts
    assert httpserver2.url_for("").rstrip("/") in hosts


def _assert_all_caches_kept(pelfs, httpserver, httpserver2):
    hosts = pelfs._get_prefix_info("/foo/bar").cache_manager._cache_list
    assert hosts == [httpserver.url_for("").rstrip("/"), httpserver2.url_for("").rstrip("/")]
    assert not [req for req, _ in httpserver2.log if req.path == "/foo/bar"]


def test_cat_retries_on_next_cache(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """A non-404 failure on the first cache is retried on the second within the same call."""
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=500)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_data("hello")

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    assert pelfs.cat("/foo/bar") == b"hello"
    _assert_first_cache_evicted(pelfs, httpserver, httpserver2)


def test_cat_all_caches_fail_raises_original_error(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """When every cache fails, the caller sees the I/O error rather than a bare NoAvailableSource."""
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=500)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_data("", status=500)

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pytest.raises(aiohttp.ClientResponseError, match="500"):
        pelfs.cat("/foo/bar")


def test_open_read_retries_on_next_cache(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """A read() failure on an open file switches to the next cache and resumes at the same offset."""
    _setup_director(httpserver, httpserver2)
    # HEAD supplies the size so fsspec builds a range-capable HTTPFile
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello world")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=500)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_handler(_serve_range(b"hello world"))

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pelfs.open("/foo/bar", "rb", cache_type="none") as fp:
        fp.seek(6)
        assert fp.read() == b"world"
    _assert_first_cache_evicted(pelfs, httpserver, httpserver2)


def test_open_stream_read_retries_on_next_cache(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """A read() failure on a sync stream that has not yet returned data switches cache."""
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_handler(_broken_body)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_data("hello")

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    # block_size=0 makes fsspec build a streaming HTTPStreamFile
    with pelfs.open("/foo/bar", "rb", block_size=0) as fp:
        assert fp.read() == b"hello"
    _assert_first_cache_evicted(pelfs, httpserver, httpserver2)


def test_open_async_read_retries_on_next_cache(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """A read() failure on an async stream that has not yet returned data switches cache."""
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=500)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_data("hello")

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    async def _read():
        fp = await pelfs.open_async("/foo/bar")
        try:
            return await fp.read()
        finally:
            await fp.close()

    assert sync(pelfs.loop, _read) == b"hello"
    _assert_first_cache_evicted(pelfs, httpserver, httpserver2)


def test_open_text_read_retries_on_next_cache(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """Text mode wraps the binary file, and its reads still switch cache."""
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=500)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_handler(_serve_range(b"hello"))

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pelfs.open("/foo/bar", "r", cache_type="none") as fp:
        assert fp.read() == "hello"
    _assert_first_cache_evicted(pelfs, httpserver, httpserver2)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_open_metadata_failure_retries_on_next_cache(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, status):
    """fsspec wraps a failed metadata GET as FileNotFoundError, which must still switch cache."""
    _setup_director(httpserver, httpserver2)
    # The working-cache probe passes; open's own HEAD and GET then fail
    httpserver.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=status)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=status)
    httpserver2.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_handler(_serve_range(b"hello"))

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pelfs.open("/foo/bar", "rb", cache_type="none") as fp:
        assert fp.read() == b"hello"
    _assert_first_cache_evicted(pelfs, httpserver, httpserver2)


def test_open_missing_object_keeps_caches(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """A 404 during open is not a cache failure, so no cache is evicted or retried."""
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=404)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=404)

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pytest.raises(FileNotFoundError):
        pelfs.open("/foo/bar", "rb")
    _assert_all_caches_kept(pelfs, httpserver, httpserver2)


@pytest.mark.parametrize("status", [401, 403])
def test_open_auth_failure_keeps_caches(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, status):
    """An authorization failure is about the credential, not the cache."""
    _setup_director(httpserver, httpserver2)
    httpserver.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=status)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=status)

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pytest.raises(FileNotFoundError):
        pelfs.open("/foo/bar", "rb")
    _assert_all_caches_kept(pelfs, httpserver, httpserver2)


@pytest.mark.parametrize("status", [400, 401, 403])
def test_cat_client_error_keeps_caches(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, status):
    """A 4xx other than 429 is raised at once and leaves every cache in place."""
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=status)

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pytest.raises(aiohttp.ClientResponseError, match=str(status)):
        pelfs.cat("/foo/bar")
    _assert_all_caches_kept(pelfs, httpserver, httpserver2)


PROPFIND_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/foo/bar</D:href>
    <D:propstat>
      <D:prop><D:getcontentlength>5</D:getcontentlength><D:resourcetype/></D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>
"""


def _webdav_fs(httpserver, get_client, get_webdav_client):
    return PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, get_webdav_client=get_webdav_client, skip_instance_cache=True)


@pytest.mark.parametrize("status", [500, 503])
def test_exists_retries_on_next_cache(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, get_webdav_client, status):
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data("", status=status)
    httpserver2.expect_request("/foo/bar", method="PROPFIND").respond_with_data(PROPFIND_RESPONSE, status=207)

    pelfs = _webdav_fs(httpserver, get_client, get_webdav_client)

    assert pelfs.exists("/foo/bar")
    _assert_first_cache_evicted(pelfs, httpserver, httpserver2)


@pytest.mark.parametrize("status", [500, 503])
def test_info_retries_on_next_cache(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, get_webdav_client, status):
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data("", status=status)
    httpserver2.expect_request("/foo/bar", method="PROPFIND").respond_with_data(PROPFIND_RESPONSE, status=207, content_type="application/xml")

    pelfs = _webdav_fs(httpserver, get_client, get_webdav_client)

    assert pelfs.info("/foo/bar")["size"] == 5
    _assert_first_cache_evicted(pelfs, httpserver, httpserver2)


@pytest.mark.parametrize(("status", "error"), [(401, UnauthorizedError), (403, AccessDeniedError)])
def test_info_auth_failure_keeps_caches(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, get_webdav_client, status, error):
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data("", status=status)

    pelfs = _webdav_fs(httpserver, get_client, get_webdav_client)

    with pytest.raises(error):
        pelfs.info("/foo/bar")
    _assert_all_caches_kept(pelfs, httpserver, httpserver2)


@pytest.mark.parametrize(
    ("kwargs", "failure"),
    [
        pytest.param({"cache_type": "none"}, _server_error, id="range"),
        pytest.param({"block_size": 0}, _empty_broken_body, id="stream"),
    ],
)
@pytest.mark.parametrize("mode", ["rb", "rt"])
def test_open_compressed_read_retries_on_next_cache(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, kwargs, failure, mode):
    """fsspec wraps the file in the codec, and its reads must still fail over."""
    data = gzip.compress(b"hello")
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data(data)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_handler(failure)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_handler(_serve_range(data))

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pelfs.open("/foo/bar", mode, compression="gzip", **kwargs) as fp:
        assert fp.read() in {b"hello", "hello"}
    _assert_first_cache_evicted(pelfs, httpserver, httpserver2)


def test_open_compressed_stream_failure_marks_cache_bad(httpserver: HTTPServer, get_client):
    """A compressed stream failure marks the cache, with no other cache to mask it."""
    data = gzip.compress(b"hello")
    _setup_director(httpserver)

    def truncated(_request):
        response = Response(iter([data[:4]]), status=200, direct_passthrough=True)
        response.headers["Content-Length"] = str(len(data))
        return response

    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data(data)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_handler(truncated)

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pelfs.open("/foo/bar", "rb", compression="gzip", block_size=0) as fp:
        # GzipFile does not close the file it wraps
        stream = fp.fileobj
        with pytest.raises(aiohttp.ClientError):
            fp.read()
    stream.close()
    assert not pelfs._get_prefix_info("/foo/bar").cache_manager._cache_list


def _stream_read(pelfs):
    with pelfs.open("/foo/bar", "rb", block_size=0) as fp:
        return fp.read()


def _async_read(pelfs):
    async def read():
        fp = await pelfs.open_async("/foo/bar")
        try:
            return await fp.read()
        finally:
            await fp.close()

    return sync(pelfs.loop, read)


@pytest.mark.parametrize("read", [pytest.param(_stream_read, id="sync"), pytest.param(_async_read, id="async")])
def test_failed_stream_reopen_closes_response(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, monkeypatch, read):
    """A stream reopen that gets an error status must not leak that response."""
    checked = []
    closed = []
    check_status = fshttp.HTTPFileSystem._raise_not_found_for_status
    close = aiohttp.ClientResponse.close

    def spy_check_status(self, response, url):
        checked.append(response)
        check_status(self, response, url)

    def spy_close(self):
        closed.append(self)
        close(self)

    monkeypatch.setattr(fshttp.HTTPFileSystem, "_raise_not_found_for_status", spy_check_status)
    monkeypatch.setattr(aiohttp.ClientResponse, "close", spy_close)

    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_handler(_broken_body)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_data("", status=503)

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pytest.raises(aiohttp.ClientResponseError, match="503"):
        read(pelfs)
    assert any(response is checked[-1] for response in closed)


def test_read_after_failed_stream_reopen_reopens(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """A read after a failed reopen must not take the closed response for end of file."""
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_handler(_broken_body)
    httpserver2.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("", status=503)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_data("hello")

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pelfs.open("/foo/bar", "rb", block_size=0) as fp:
        with pytest.raises(aiohttp.ClientResponseError, match="503"):
            fp.read()
        assert fp.read() == b"hello"


@pytest.mark.parametrize("status", [400, 403])
def test_non_retryable_failure_is_recorded(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, status):
    """A failure that keeps the cache still appears in the access data."""
    url, _ = _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=200)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=status)

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pytest.raises(aiohttp.ClientResponseError, match=str(status)):
        pelfs.cat("/foo/bar")
    responses, _ = pelfs.get_access_data().get_responses("/foo/bar")
    assert [(r.access_path, r.success) for r in responses] == [(url, False)]
    assert str(status) in responses[0].error
    _assert_all_caches_kept(pelfs, httpserver, httpserver2)


def test_missing_object_is_not_recorded_as_failure(httpserver: HTTPServer, get_client):
    _setup_director(httpserver)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=200)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=404)

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pytest.raises(FileNotFoundError):
        pelfs.cat("/foo/bar")
    assert pelfs.get_access_data().get_responses("/foo/bar") == ([], False)


@pytest.mark.parametrize(
    "read",
    [
        pytest.param(lambda pelfs: pelfs.open("/foo/bar", "rb", cache_type="none").read(), id="range"),
        pytest.param(_stream_read, id="stream"),
        pytest.param(_async_read, id="async"),
    ],
)
def test_read_failover_records_success(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, read):
    """A read that recovered on the next cache records that cache's success."""
    urls = _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_handler(_broken_body)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_handler(_serve_range(b"hello"))

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    assert read(pelfs) == b"hello"
    responses, _ = pelfs.get_access_data().get_responses("/foo/bar")
    assert (responses[-1].access_path, responses[-1].success) == (urls[1], True)


@pytest.mark.parametrize("status", [401, 403])
def test_probe_auth_failure_reports_real_error(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, status):
    """A probe refused for its credential keeps the cache, so the caller sees the real error."""
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=status)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=status)

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    with pytest.raises(aiohttp.ClientResponseError, match=str(status)):
        pelfs.cat("/foo/bar")
    _assert_all_caches_kept(pelfs, httpserver, httpserver2)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_probe_retryable_status_skips_cache(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, status):
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=status)
    httpserver2.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_data("hello")

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    assert pelfs.cat("/foo/bar") == b"hello"
    _assert_first_cache_evicted(pelfs, httpserver, httpserver2)


class _ClosingHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.recv(1024)


def test_probe_disconnect_skips_cache(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """A cache that drops the connection after accepting it is skipped."""
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), _ClosingHandler) as closing:
        threading.Thread(target=closing.serve_forever, daemon=True).start()
        urls = [f"http://127.0.0.1:{closing.server_address[1]}/foo/bar", httpserver2.url_for("/foo/bar")]
        link = ", ".join(f'<{url}>; rel="duplicate"; pri={i}; depth=1' for i, url in enumerate(urls, 1))
        httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
        httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
            "",
            status=307,
            headers={"Link": link, "Location": urls[0], "X-Pelican-Namespace": "namespace=/foo"},
        )
        httpserver2.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
        httpserver2.expect_request("/foo/bar", method="GET").respond_with_data("hello")

        pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

        assert pelfs.cat("/foo/bar") == b"hello"
        closing.shutdown()


def _wrapped(cause):
    error = FileNotFoundError("/foo/bar")
    error.__cause__ = cause
    return error


def _response_error(status):
    return aiohttp.ClientResponseError(None, (), status=status)


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (connection := aiohttp.ClientConnectionError("refused"), connection),
        (_wrapped(connection), connection),
        (refused := ConnectionRefusedError(), refused),
        (too_many := _response_error(429), too_many),
        (no_connection := NoConnectionError("host"), no_connection),
        (webdav_server_error := ResponseErrorCodeError("url", 503, "busy"), webdav_server_error),
        (server_error := _response_error(500), server_error),
        (_wrapped(server_error), server_error),
        (_wrapped(timeout := TimeoutError()), timeout),
        (_response_error(404), None),
        (_response_error(400), None),
        (_response_error(401), None),
        (_wrapped(_response_error(403)), None),
        (ResponseErrorCodeError("url", 400, "bad"), None),
        (UnauthorizedError("host"), None),
        (AccessDeniedError("host"), None),
        (RemoteResourceNotFoundError("/foo/bar"), None),
        (ConnectionExceptionError(_response_error(403)), None),
        (OSError(errno.ENOSPC, "No space left on device"), None),
        (PermissionError(), None),
        (_wrapped(_response_error(404)), None),
        (FileNotFoundError("/foo/bar"), None),
        (_wrapped(ValueError("bad header")), None),
        (ValueError("not a cache problem"), None),
    ],
)
def test_cache_failure(error, failure):
    assert _cache_failure(error) is failure


def test_non_transport_error_does_not_evict_caches(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, monkeypatch):
    """An error that is not a cache failure is raised at once and leaves every cache in place."""
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    async def broken_cat_file(*args, **kwargs):
        msg = "not a cache problem"
        raise ValueError(msg)

    monkeypatch.setattr(pelfs.http_file_system, "_cat_file", broken_cat_file)

    with pytest.raises(ValueError, match="not a cache problem"):
        pelfs.cat("/foo/bar")
    _assert_all_caches_kept(pelfs, httpserver, httpserver2)


def test_retry_stops_when_cache_is_not_evicted(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, monkeypatch):
    """If a failed cache stays in the list, the retry loop still stops."""
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=500)

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)
    monkeypatch.setattr(pelfs, "_bad_cache", lambda *_: None)

    with pytest.raises(aiohttp.ClientResponseError, match="500"):
        pelfs.cat("/foo/bar")
    # One GET is the director redirect
    assert len([req for req, _ in httpserver.log if req.method == "GET" and req.path == "/foo/bar"]) == MAX_CACHE_ATTEMPTS + 1


def test_retry_names_error_without_message(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, monkeypatch, caplog):
    """An error with an empty str, like a bare TimeoutError, is reported by its type."""
    _setup_director(httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")

    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)
    results = iter([TimeoutError(), b"hello"])

    async def flaky_cat_file(*args, **kwargs):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(pelfs.http_file_system, "_cat_file", flaky_cat_file)

    with caplog.at_level("WARNING", logger="fsspec.pelican"):
        assert pelfs.cat("/foo/bar") == b"hello"

    assert "(TimeoutError); retrying on" in caplog.text
    responses, _ = pelfs.get_access_data().get_responses("/foo/bar")
    assert responses[0].error == "TimeoutError"
