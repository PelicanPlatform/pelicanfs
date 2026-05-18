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
from datetime import datetime, timedelta, timezone

from pytest_httpserver import HTTPServer

import pelicanfs.core


def test_authorization_headers(httpserver: HTTPServer, get_client, monkeypatch):
    foo_bar_url = httpserver.url_for("/foo/bar")
    test_headers_with_bearer = {"Authorization": "Bearer test"}

    # Mock token validation to accept the test token
    def mock_token_validation(*args, **kwargs):
        return True, datetime.now(timezone.utc) + timedelta(hours=1)

    monkeypatch.setattr("pelicanfs.token_generator.token_is_valid_and_acceptable", mock_token_validation)

    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "Location": foo_bar_url,
            "X-Pelican-Namespace": "namespace=/foo, require-token=true",
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


def test_token_set_in_http_filesystem_kwargs(httpserver: HTTPServer, get_client):
    """
    Test that tokens passed via headers are set in http_file_system.kwargs.

    Regression test: Ensures that when HTTPFileSystem makes requests, it includes
    the Authorization header. Previously tokens were not properly propagated to
    http_file_system.kwargs, causing 403 errors.
    """
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    test_token = "Bearer test_token_123"
    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        headers={"Authorization": test_token},
    )

    # Token should be set in self.token
    assert pelfs.token == test_token

    # Token should also be in http_file_system.kwargs for requests
    http_headers = pelfs.http_file_system.kwargs.get("headers", {})
    assert http_headers.get("Authorization") == test_token


def test_set_http_filesystem_token_updates_kwargs(httpserver: HTTPServer, get_client):
    """
    Test that _set_http_filesystem_token properly updates http_file_system.kwargs.

    Regression test: This method is called after token generation to ensure tokens
    are included in subsequent HTTP requests.
    """
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    # Initially no token
    assert pelfs.http_file_system.kwargs.get("headers", {}).get("Authorization") is None

    # Set a token
    pelfs._set_http_filesystem_token("new_token_456")

    # Token should now be in http_file_system.kwargs
    http_headers = pelfs.http_file_system.kwargs.get("headers", {})
    assert http_headers.get("Authorization") == "Bearer new_token_456"


def test_get_token_returns_token_without_bearer_prefix(httpserver: HTTPServer, get_client):
    """
    Test that _get_token() returns the token value without the Bearer prefix.
    """
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        headers={"Authorization": "Bearer my_token"},
    )

    # _get_token should return the raw token value
    token = pelfs._get_token()
    assert token == "my_token"
