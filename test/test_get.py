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


def test_get_directory_recursive(httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response):
    """
    Test that .get() with recursive=True can handle directory paths.

    After fixing the 409 Conflict issue, PROPFIND requests now go to the
    collections server which properly handles directory listings.
    """
    foo_bar_url = httpserver.url_for("foo/bar")

    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}",
        },
    )

    # PROPFIND request with trailing slash now succeeds
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response, status=207)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    # Test that listing works (which is what .get() does internally)
    result = pelfs.ls("/foo/bar", detail=False)
    assert isinstance(result, (list, set))


def test_ls_directory_with_trailing_slash_conflict(httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response):
    """
    Test that _ls_real properly lists directories with PROPFIND to collections server.

    After the fix, PROPFIND requests are sent to the collections server which
    properly handles directory listings with trailing slashes.
    """
    foo_bar_url = httpserver.url_for("foo/bar")

    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    # First request without trailing slash gets redirect with collections-url
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}",
        },
    )

    # PROPFIND to collections server now succeeds
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response, status=207)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    # Call ls - should successfully list directory
    result = pelfs.ls("/foo/bar", detail=False)
    assert isinstance(result, (list, set))


def test_ls_directory_not_found(httpserver: HTTPServer, get_client, get_webdav_client):
    """
    Test that when a directory doesn't exist, we get a FileNotFoundError.
    """
    foo_bar_url = httpserver.url_for("foo/bar")

    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}",
        },
    )

    # PROPFIND returns 404 for non-existent directory
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data("Not found", status=404)
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data("Not found", status=404)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    # Should raise FileNotFoundError for non-existent directory
    try:
        pelfs.ls("/foo/bar", detail=False)
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError:
        pass  # Expected
