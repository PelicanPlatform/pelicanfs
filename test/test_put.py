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
import pytest
from aiohttp import ClientResponseError
from pytest_httpserver import HTTPServer

from pelicanfs.core import PelicanFileSystem
from pelicanfs.exceptions import NoAvailableSource


def test_put(httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response):
    foo_bar_url = httpserver.url_for("/foo/bar/test.py")
    base_url = httpserver.url_for("/")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": base_url})
    httpserver.expect_oneshot_request("/foo/bar/test.py").respond_with_data(
        "",
        status=307,
        headers={
            "Link": '<"some.other.url">; rel="duplicate"; pri=1; depth=1',
            "Location": foo_bar_url,
            "X-Pelican-Namespace": f"namespace=/foo, collections-url={base_url}",
        },
    )
    httpserver.expect_request("/foo/bar/test.py/", method="PROPFIND").respond_with_data("not a directory", status=500)
    httpserver.expect_request("/foo/bar/test.py", method="PROPFIND").respond_with_data(status=404)
    httpserver.expect_oneshot_request("/foo/bar/test.py", method="PUT").respond_with_data(status=200)
    httpserver.expect_request("/api/v1.0/director/origin/foo/bar/test.py").respond_with_data(
        "",
        status=200,
        headers={
            "Location": foo_bar_url,
        },
    )

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )
    pelfs.put("test_put.py", "/foo/bar/test.py")


def test_put_dest_dir(httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response):
    foo_bar_url = httpserver.url_for("/foo/bar/")
    base_url = httpserver.url_for("/")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": base_url})
    httpserver.expect_oneshot_request("/foo/bar/", method="PUT").respond_with_data(status=200)
    httpserver.expect_request("/api/v1.0/director/origin/foo/bar/test_put.py").respond_with_data(
        "",
        status=200,
        headers={
            "Location": foo_bar_url,
        },
    )

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )
    pelfs.put("test_put.py", "/foo/bar/")


def test_put_no_available_source(httpserver: HTTPServer, get_client, get_webdav_client):
    base_url = httpserver.url_for("/")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": base_url})

    # Raise a 404 like a director would if the origin was not found
    httpserver.expect_oneshot_request("/foo/bar/test.py").respond_with_data(
        "",
        status=404,
    )

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )
    with pytest.raises(NoAvailableSource):
        pelfs.put("test_put.py", "/foo/bar/")


def test_put_permission_denied(httpserver: HTTPServer, get_client, get_webdav_client):
    foo_bar_url = httpserver.url_for("/foo/bar/")
    base_url = httpserver.url_for("/")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": base_url})
    httpserver.expect_oneshot_request("/foo/bar/", method="PUT").respond_with_data(status=403)
    httpserver.expect_request("/api/v1.0/director/origin/foo/bar/test_put.py").respond_with_data(
        "",
        status=200,
        headers={
            "Location": foo_bar_url,
        },
    )

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )
    with pytest.raises(ClientResponseError):
        pelfs.put("test_put.py", "/foo/bar/")
