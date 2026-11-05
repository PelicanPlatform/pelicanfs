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

Tests that assert which Authorization header reaches the server.
"""

import gc
import weakref
from datetime import UTC, datetime, timedelta

import pytest
from fsspec.asyn import get_loop, sync
from pytest_httpserver import HTTPServer

from pelicanfs.core import PelicanFileSystem
from pelicanfs.token_generator import TokenGenerator, TokenInfo

GENERATED = "Bearer gen-token"


def requests_to(server: HTTPServer, method: str):
    return [request for request, _ in server.log if request.method == method]


@pytest.fixture(name="generated_token")
def fixture_generated_token(monkeypatch):
    def get_token(self):
        self.token = TokenInfo("gen-token", datetime.now(UTC) + timedelta(hours=1))
        return "gen-token"

    monkeypatch.setattr(TokenGenerator, "get_token", get_token)


@pytest.fixture(name="federation")
def fixture_federation(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """
    A require-token namespace with the director on `httpserver` and the cache on
    `httpserver2`. Returns a factory for filesystems that use this federation.
    """
    httpserver2.clear()
    cache_url = httpserver2.url_for("/foo/bar")

    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{cache_url}>; rel="duplicate"; pri=1; depth=1',
            "Location": cache_url,
            "X-Pelican-Namespace": "namespace=/foo, require-token=true",
        },
    )
    httpserver2.expect_request("/foo/bar").respond_with_data("hello, world!")

    def make_fs(**kwargs):
        return PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, **kwargs)

    return make_fs


@pytest.mark.usefixtures("generated_token")
def test_open_async_read_sends_token(federation, httpserver2):
    async def read():
        fp = await federation(asynchronous=True).open_async("/foo/bar")
        try:
            return await fp.read()
        finally:
            await fp.close()

    # On fsspec's loop, so the get_client fixture can close the session on its own loop
    assert sync(get_loop(), read) == b"hello, world!"
    assert [request.headers.get("Authorization") for request in requests_to(httpserver2, "GET")] == [GENERATED]


@pytest.mark.usefixtures("generated_token")
def test_per_call_headers_keep_token(federation, httpserver2):
    assert federation().cat_file("/foo/bar", headers={"X-Foo": "1"}) == b"hello, world!"

    request = requests_to(httpserver2, "GET")[-1]
    assert request.headers.get("Authorization") == GENERATED
    assert request.headers.get("X-Foo") == "1"


@pytest.mark.usefixtures("generated_token")
def test_per_call_authorization_wins(federation, httpserver2):
    federation().cat_file("/foo/bar", headers={"authorization": "Bearer mine"})

    assert requests_to(httpserver2, "GET")[-1].headers.get("Authorization") == "Bearer mine"


@pytest.mark.usefixtures("generated_token")
def test_open_with_per_call_headers(federation, httpserver2):
    with federation().open("/foo/bar", "rb", headers={"X-Foo": "1"}) as fp:
        assert fp.read() == b"hello, world!"

    request = requests_to(httpserver2, "GET")[-1]
    assert request.headers.get("Authorization") == GENERATED
    assert request.headers.get("X-Foo") == "1"


@pytest.mark.usefixtures("generated_token")
def test_pipe_file_after_read_sends_token(federation, httpserver2):
    pelfs = federation()
    pelfs.cat_file("/foo/bar")

    pelfs.pipe_file(httpserver2.url_for("/foo/bar"), b"x")

    assert requests_to(httpserver2, "PUT")[-1].headers.get("Authorization") == GENERATED


@pytest.mark.usefixtures("generated_token")
def test_generated_token_stays_out_of_caller_headers(federation):
    headers = {"X-Foo": "1"}
    pelfs = federation(headers=headers)

    pelfs.cat_file("/foo/bar")

    assert headers == {"X-Foo": "1"}
    assert "gen-token" not in repr(pelfs.storage_options)


@pytest.mark.usefixtures("generated_token")
def test_failed_regeneration_sends_no_stale_token(federation, httpserver2, monkeypatch):
    pelfs = federation()
    pelfs.cat_file("/foo/bar")

    def fail(_self):
        msg = "no token"
        raise RuntimeError(msg)

    monkeypatch.setattr(TokenGenerator, "get_token", fail)
    pelfs._token_expiry = datetime.now(UTC) - timedelta(hours=1)

    pelfs.cat_file("/foo/bar")

    assert requests_to(httpserver2, "GET")[-1].headers.get("Authorization") is None


@pytest.mark.usefixtures("generated_token")
def test_lowercase_caller_authorization_is_the_token(federation, httpserver2):
    pelfs = federation(headers={"authorization": "Bearer mine"})

    pelfs.cat_file("/foo/bar")

    assert pelfs.token == "Bearer mine"
    assert requests_to(httpserver2, "GET")[-1].headers.getlist("Authorization") == ["Bearer mine"]


@pytest.mark.usefixtures("generated_token")
def test_director_never_receives_token(federation, httpserver, httpserver2):
    pelfs = federation()
    pelfs.cat_file("/foo/bar")
    with pelfs._namespace_lock:
        pelfs._namespace_cache.clear()
    pelfs.cat_file("/foo/bar")

    assert requests_to(httpserver2, "GET")
    assert [request.headers.get("Authorization") for request, _ in httpserver.log] == [None] * len(httpserver.log)


@pytest.mark.usefixtures("generated_token")
def test_session_does_not_keep_filesystem_alive(federation):
    pelfs = federation()
    pelfs.cat_file("/foo/bar")
    session = sync(pelfs.loop, pelfs.http_file_system.set_session)
    ref = weakref.ref(pelfs)

    del pelfs
    gc.collect()

    assert ref() is None
    assert session.closed
