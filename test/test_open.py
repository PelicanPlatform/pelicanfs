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
import aiohttp
import pytest
from pytest_httpserver import HTTPServer

import pelicanfs.core
from pelicanfs.core import PelicanFileSystem
from pelicanfs.exceptions import NoAvailableSource


def test_open(httpserver: HTTPServer, get_client):
    foo_bar_url = httpserver.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "Location": foo_bar_url,
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data("hello, world!")
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("hello, world!")

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    assert pelfs.cat("/foo/bar") == b"hello, world!"


def test_open_multiple_servers(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    foo_bar_url = httpserver2.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "Location": foo_bar_url,
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver2.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data("hello, world 2")
    httpserver2.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("hello, world 2")

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )
    assert pelfs.cat("/foo/bar") == b"hello, world 2"


def test_open_fallback(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    foo_bar_url = httpserver.url_for("/foo/bar")
    foo_bar_url2 = httpserver2.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1, ' f'<{foo_bar_url2}>; rel="duplicate"; pri=2; depth=1',
            "Location": foo_bar_url,
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver2.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data("hello, world 2")
    httpserver2.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("hello, world 2")
    httpserver2.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("hello, world 2")

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )
    assert pelfs.cat("/foo/bar") == b"hello, world 2"
    assert pelfs.cat("/foo/bar") == b"hello, world 2"
    with pytest.raises(aiohttp.ClientResponseError):
        pelfs.cat("/foo/bar")
    with pytest.raises(NoAvailableSource):
        assert pelfs.cat("/foo/bar")

    response, e = pelfs.get_access_data().get_responses("/foo/bar")
    assert e
    assert len(response) == 3
    assert response[2].success is False


def test_open_preferred(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    foo_bar_url = httpserver.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "Location": foo_bar_url,
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver2.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data("hello, world")
    httpserver2.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("hello, world")

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        preferred_caches=[httpserver2.url_for("/")],
    )
    assert pelfs.cat("/foo/bar") == b"hello, world"


def test_open_preferred_plus(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    foo_bar_url = httpserver.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "Location": foo_bar_url,
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver2.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data("hello, world")
    httpserver2.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("hello, world", status=500)
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("hello, world")

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        preferred_caches=[httpserver2.url_for("/"), "+"],
    )
    with pytest.raises(aiohttp.ClientResponseError):
        pelfs.cat("/foo/bar")

    assert pelfs.cat("/foo/bar") == b"hello, world"


def test_open_mapper(httpserver: HTTPServer, get_client):
    foo_url = httpserver.url_for("/foo")
    foo_bar_url = httpserver.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo", method="GET").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_url}>; rel="duplicate"; pri=1; depth=1',
            "Location": foo_url,
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver.expect_request("/foo", method="HEAD").respond_with_data("hello, world!")

    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "Location": foo_bar_url,
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )

    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello, world!")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("hello, world!")

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    pel_map = pelicanfs.core.PelicanMap("/foo", pelfs=pelfs)
    assert pel_map["bar"] == b"hello, world!"
