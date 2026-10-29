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

import aiohttp
import pytest
from fsspec.asyn import sync
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Response

from pelicanfs.core import PelicanFileSystem
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


def _assert_first_cache_evicted(pelfs, httpserver, httpserver2):
    namespace_info = pelfs._get_prefix_info("/foo/bar")
    assert namespace_info is not None
    hosts = namespace_info.cache_manager._cache_list
    assert httpserver.url_for("").rstrip("/") not in hosts
    assert httpserver2.url_for("").rstrip("/") in hosts


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
