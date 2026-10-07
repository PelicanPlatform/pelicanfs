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
import pytest
from pytest_httpserver import HTTPServer

from pelicanfs.core import PelicanFileSystem


def _setup_director(httpserver, path="/foo/bar"):
    """Register the pelican-configuration and a single director 307 redirect for path."""
    url = httpserver.url_for(path)
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request(path, method="GET").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{url}>; rel="duplicate"; pri=1; depth=1',
            "Location": url,
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    return url


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
    from fsspec.asyn import sync

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
        return await fp.read()

    with pytest.raises(FileNotFoundError):
        sync(pelfs.loop, _read)

    namespace_info = pelfs._get_prefix_info("/foo/bar")
    assert namespace_info is not None
    assert len(namespace_info.cache_manager._cache_list) > 0


def test_409_head_on_collection_keeps_cache(httpserver: HTTPServer, get_client):
    """A 409 HEAD (OSDF caches answer this for collections) must count as a working cache."""
    from fsspec.asyn import sync

    url = _setup_director(httpserver, path="/foo/dir")
    httpserver.expect_oneshot_request("/foo/dir", method="HEAD").respond_with_data("", status=409)

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    cache_url, _ = sync(pelfs.loop, pelfs.get_working_cache, "/foo/dir")

    assert cache_url == url
    namespace_info = pelfs._get_prefix_info("/foo/dir")
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

    with pytest.raises(Exception):
        pelfs.cat("/foo/bar")
    # Second call must not crash with ValueError even though the cache is already evicted
    with pytest.raises(Exception):
        pelfs.cat("/foo/bar")
