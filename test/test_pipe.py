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
from pelicanfs.token_generator import TokenGenerator

ORIGIN_LOOKUP = "/api/v1.0/director/origin"


def _expect_origin(httpserver: HTTPServer, path: str, **extra_headers):
    """Make the director answer an origin lookup for ``path`` with the origin's URL for it."""
    httpserver.expect_request(ORIGIN_LOOKUP + path).respond_with_data(
        "",
        status=200,
        headers={"Location": httpserver.url_for(path), **extra_headers},
    )


def _make_pelfs(httpserver: HTTPServer, get_client, get_webdav_client, **kwargs):
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    return PelicanFileSystem(
        kwargs.pop("discovery_url", httpserver.url_for("/")),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
        **kwargs,
    )


def _requests_to(httpserver: HTTPServer, path: str):
    return [request.method for request, _ in httpserver.log if request.path == path]


def test_pipe(httpserver: HTTPServer, get_client, get_webdav_client):
    _expect_origin(httpserver, "/foo/bar/test.txt")
    httpserver.expect_oneshot_request("/foo/bar/test.txt", method="PUT", data=b"hello, world!").respond_with_data(status=200)
    pelfs = _make_pelfs(httpserver, get_client, get_webdav_client)

    pelfs.pipe("/foo/bar/test.txt", b"hello, world!")

    httpserver.check_assertions()
    # The director was asked for the origin, and the only request to the object was the PUT it pointed at
    assert _requests_to(httpserver, ORIGIN_LOOKUP + "/foo/bar/test.txt") == ["GET"]
    assert _requests_to(httpserver, "/foo/bar/test.txt") == ["PUT"]


def test_pipe_file_url_form(httpserver: HTTPServer, get_client, get_webdav_client):
    _expect_origin(httpserver, "/foo/bar/test.txt")
    httpserver.expect_oneshot_request("/foo/bar/test.txt", method="PUT", data=b"hello, world!").respond_with_data(status=200)
    federation = f"pelican://localhost:{httpserver.port}"
    pelfs = _make_pelfs(httpserver, get_client, get_webdav_client, discovery_url=federation)

    pelfs.pipe_file(f"{federation}/foo/bar/test.txt", b"hello, world!")

    httpserver.check_assertions()
    assert _requests_to(httpserver, "/foo/bar/test.txt") == ["PUT"]


def test_pipe_multiple(httpserver: HTTPServer, get_client, get_webdav_client):
    _expect_origin(httpserver, "/foo/bar/a.txt")
    _expect_origin(httpserver, "/foo/bar/b.txt")
    httpserver.expect_oneshot_request("/foo/bar/a.txt", method="PUT", data=b"a").respond_with_data(status=200)
    httpserver.expect_oneshot_request("/foo/bar/b.txt", method="PUT", data=b"b").respond_with_data(status=200)
    pelfs = _make_pelfs(httpserver, get_client, get_webdav_client)

    pelfs.pipe({"/foo/bar/a.txt": b"a", "/foo/bar/b.txt": b"b"})

    httpserver.check_assertions()
    assert _requests_to(httpserver, "/foo/bar/a.txt") == ["PUT"]
    assert _requests_to(httpserver, "/foo/bar/b.txt") == ["PUT"]


def test_pipe_create_mode(httpserver: HTTPServer, get_client, get_webdav_client):
    _expect_origin(httpserver, "/foo/bar/test.txt")
    httpserver.expect_oneshot_request("/foo/bar/test.txt", method="PUT", data=b"hello, world!").respond_with_data(status=200)
    pelfs = _make_pelfs(httpserver, get_client, get_webdav_client)

    pelfs.pipe("/foo/bar/test.txt", b"hello, world!", mode="create")

    httpserver.check_assertions()
    assert _requests_to(httpserver, "/foo/bar/test.txt") == ["PUT"]


def test_pipe_rejects_append(httpserver: HTTPServer, get_client, get_webdav_client):
    pelfs = _make_pelfs(httpserver, get_client, get_webdav_client)

    with pytest.raises(ValueError):
        pelfs.pipe("/foo/bar/test.txt", b"x", mode="append")

    # The mode is rejected before anything is asked of the federation
    assert httpserver.log == []


def test_pipe_no_available_source(httpserver: HTTPServer, get_client, get_webdav_client):
    # Answer like a director would if no origin serves the namespace
    httpserver.expect_oneshot_request(ORIGIN_LOOKUP + "/foo/bar/test.txt").respond_with_data("", status=404)
    pelfs = _make_pelfs(httpserver, get_client, get_webdav_client)

    with pytest.raises(NoAvailableSource):
        pelfs.pipe("/foo/bar/test.txt", b"hello, world!")

    assert _requests_to(httpserver, "/foo/bar/test.txt") == []


def test_pipe_permission_denied(httpserver: HTTPServer, get_client, get_webdav_client):
    _expect_origin(httpserver, "/foo/bar/test.txt")
    httpserver.expect_oneshot_request("/foo/bar/test.txt", method="PUT").respond_with_data(status=403)
    pelfs = _make_pelfs(httpserver, get_client, get_webdav_client)

    with pytest.raises(ClientResponseError):
        pelfs.pipe("/foo/bar/test.txt", b"hello, world!")


def test_pipe_sends_token(httpserver: HTTPServer, get_client, get_webdav_client):
    token_header = {"Authorization": "Bearer test"}
    _expect_origin(httpserver, "/foo/bar/test.txt", **{"X-Pelican-Namespace": "namespace=/foo, require-token=true"})
    # Only a PUT carrying the token matches; an untokened PUT would be answered 500 by the server
    httpserver.expect_oneshot_request("/foo/bar/test.txt", method="PUT", headers=token_header, data=b"hello, world!").respond_with_data(status=200)
    pelfs = _make_pelfs(httpserver, get_client, get_webdav_client, headers=token_header)

    pelfs.pipe("/foo/bar/test.txt", b"hello, world!")

    httpserver.check_assertions()
    assert _requests_to(httpserver, "/foo/bar/test.txt") == ["PUT"]


def test_pipe_sends_generated_token(httpserver: HTTPServer, get_client, get_webdav_client, monkeypatch):
    """
    A token pelicanfs generates itself must reach the PUT, not only one given at construction.

    Only the token flow is stubbed; the director exchange and the upload are the real thing.
    The tokened PUT is registered first, so an untokened PUT falls through to the 401 below
    and the server is what decides whether the token was applied.
    """
    monkeypatch.setattr(TokenGenerator, "get_token", lambda self: "generated-token")
    _expect_origin(httpserver, "/foo/bar/test.txt", **{"X-Pelican-Namespace": "namespace=/foo, require-token=true"})
    httpserver.expect_request("/foo/bar/test.txt", method="PUT", headers={"Authorization": "Bearer generated-token"}).respond_with_data(status=200)
    httpserver.expect_request("/foo/bar/test.txt", method="PUT").respond_with_data(status=401)
    pelfs = _make_pelfs(httpserver, get_client, get_webdav_client)
    assert pelfs.token is None, "this test only means something if the token has to be generated"

    pelfs.pipe("/foo/bar/test.txt", b"hello, world!")

    assert [response.status_code for request, response in httpserver.log if request.path == "/foo/bar/test.txt"] == [200]
