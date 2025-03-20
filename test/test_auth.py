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
from pytest_httpserver import HTTPServer

import pelicanfs.core


def test_authorization_headers(httpserver: HTTPServer, get_client):
    foo_bar_url = httpserver.url_for("/foo/bar")
    test_headers_with_bearer = {"Authorization": "Bearer test"}

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

    httpserver.expect_request("/foo/bar", headers=test_headers_with_bearer, method="HEAD").respond_with_data("hello, world!")
    httpserver.expect_request("/foo/bar", headers=test_headers_with_bearer, method="GET").respond_with_data("hello, world!")

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        headers=test_headers_with_bearer,
    )

    assert pelfs.cat("/foo/bar", headers={"Authorization": "Bearer test"}) == b"hello, world!"


def test_authz_query(httpserver: HTTPServer, get_client):
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

    httpserver.expect_request("/foo/bar", query_string="authz=test", method="HEAD").respond_with_data("hello, world!")
    httpserver.expect_request("/foo/bar", query_string="authz=test", method="GET").respond_with_data("hello, world!")

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    assert pelfs.cat("/foo/bar?authz=test") == b"hello, world!"
