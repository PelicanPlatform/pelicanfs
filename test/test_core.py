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

Tests for PelicanFileSystem core functionality and initialization.
"""
from pytest_httpserver import HTTPServer

import pelicanfs.core


def test_multiple_pelfs_instances_have_separate_http_filesystems(httpserver: HTTPServer, get_client):
    """
    Test that creating multiple PelicanFileSystem instances results in
    separate HTTPFileSystem instances, not shared cached ones.

    Regression test for HTTPFileSystem caching bug: Without skip_instance_cache=True,
    multiple PelicanFileSystem instances would share the same HTTPFileSystem,
    causing method binding issues where tokens set in one instance weren't
    available in another.
    """
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    pelfs1 = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    pelfs2 = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    # Each PelicanFileSystem should have its own HTTPFileSystem instance
    assert pelfs1.http_file_system is not pelfs2.http_file_system, "Multiple PelicanFileSystem instances should not share HTTPFileSystem instances"


def test_ls_method_binding_isolated_between_instances(httpserver: HTTPServer, get_client):
    """
    Test that _ls_from_http method binding is isolated between PelicanFileSystem instances.

    Regression test: Without the fix, calling pelfs2._ls_from_http could inadvertently
    use pelfs1's state because they shared the same HTTPFileSystem.
    """
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    pelfs1 = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    pelfs2 = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    # The _ls method on each http_file_system should be bound to its respective PelicanFileSystem
    assert pelfs1.http_file_system._ls.__self__ is pelfs1, "pelfs1.http_file_system._ls should be bound to pelfs1"
    assert pelfs2.http_file_system._ls.__self__ is pelfs2, "pelfs2.http_file_system._ls should be bound to pelfs2"


def test_token_state_isolated_between_instances(httpserver: HTTPServer, get_client):
    """
    Test that token state is isolated between PelicanFileSystem instances.

    Regression test: Without the fix, setting a token on pelfs1 could be
    overwritten or lost when pelfs2 was created because they shared the
    same HTTPFileSystem.
    """
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    pelfs1 = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        headers={"Authorization": "Bearer token1"},
    )

    pelfs2 = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        headers={"Authorization": "Bearer token2"},
    )

    # Each instance should maintain its own token
    assert pelfs1.token == "Bearer token1"
    assert pelfs2.token == "Bearer token2"

    # The HTTPFileSystem kwargs should also be separate
    pelfs1_auth = pelfs1.http_file_system.kwargs.get("headers", {}).get("Authorization")
    pelfs2_auth = pelfs2.http_file_system.kwargs.get("headers", {}).get("Authorization")

    assert pelfs1_auth == "Bearer token1"
    assert pelfs2_auth == "Bearer token2"
