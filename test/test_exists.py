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


def test_exists_nonexistent_object(httpserver: HTTPServer, get_client):
    """
    Test that exists() returns False when cache returns 404 for a non-existent object.
    
    This tests the fix in get_working_cache() where 404 responses from caches
    are now accepted as valid (indicating the cache is working, but the object
    doesn't exist), rather than causing the cache to be marked as bad.
    """
    foo_bar_url = httpserver.url_for("/foo/bar")
    
    # Mock the pelican configuration endpoint
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json(
        {"director_endpoint": httpserver.url_for("/")}
    )
    
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
    
    # Mock the actual exists check (GET request) returning 404
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=404,
    )

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    # The key assertion: exists() should return False, not raise an exception
    assert pelfs.exists("/foo/bar") is False


def test_exists_existing_object(httpserver: HTTPServer, get_client):
    """
    Test that exists() returns True when cache returns 200 for an existing object.
    
    This is a complementary test to ensure the normal case still works correctly.
    """
    foo_bar_url = httpserver.url_for("/foo/bar")
    
    # Mock the pelican configuration endpoint
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json(
        {"director_endpoint": httpserver.url_for("/")}
    )
    
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
    
    # Mock the actual exists check (GET request)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data(
        "hello, world!",
        status=200,
    )

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    # exists() should return True for an existing object
    assert pelfs.exists("/foo/bar") is True
