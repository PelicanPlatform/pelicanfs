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

Tests for the sys.audit events.
"""

import sys
from datetime import UTC, datetime, timedelta

import pytest
from fsspec.asyn import get_loop, sync
from pytest_httpserver import HTTPServer

from pelicanfs.core import PelicanFileSystem, _audit_url
from pelicanfs.token_generator import TokenGenerator, TokenInfo

# A hook cannot be removed, so this one stays installed and does nothing between tests.
_events: list[tuple[str, tuple]] | None = None
_veto: str | None = None
_veto_path: str | None = None


def _hook(event, args):
    if _events is not None and event.startswith("pelicanfs."):
        _events.append((event, args))
        if event == _veto and (_veto_path is None or args[0] == _veto_path):
            raise PermissionError(event)


sys.addaudithook(_hook)


@pytest.fixture(name="events")
def fixture_events():
    global _events, _veto, _veto_path  # noqa: PLW0603
    _events = []
    yield _events
    _events = None
    _veto = None
    _veto_path = None


@pytest.fixture(name="generated_token")
def fixture_generated_token(monkeypatch):
    def get_token(self):
        self.token = TokenInfo("gen-token", datetime.now(UTC) + timedelta(hours=1))
        return "gen-token"

    monkeypatch.setattr(TokenGenerator, "get_token", get_token)


@pytest.fixture(name="make_fs")
def fixture_make_fs(httpserver: HTTPServer, get_client, get_webdav_client):
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    def make_fs(namespace="namespace=/foo"):
        url = httpserver.url_for("/foo/bar")
        httpserver.expect_request("/foo/bar", method="GET").respond_with_data(
            "",
            status=307,
            headers={"Link": f'<{url}>; rel="duplicate"; pri=1; depth=1', "Location": url, "X-Pelican-Namespace": namespace},
        )
        return PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, get_webdav_client=get_webdav_client, skip_instance_cache=True)

    return make_fs


@pytest.fixture(name="federation")
def fixture_federation(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """A require-token namespace with the director on `httpserver` and the cache on `httpserver2`."""
    httpserver2.clear()
    cache_url = httpserver2.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{cache_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": "namespace=/foo, require-token=true"},
    )
    httpserver2.expect_request("/foo/bar").respond_with_data("hello, world!")
    return PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("/foo/bar", "/foo/bar"),
        ("/foo/bar?authz=secret", "/foo/bar"),
        ("https://user:secret@host:8443/foo?authz=secret#frag", "https://host:8443/foo"),
    ],
)
def test_audit_url(url, expected):
    assert _audit_url(url) == expected


@pytest.mark.usefixtures("generated_token")
def test_read_events(federation, httpserver: HTTPServer, httpserver2: HTTPServer, events):
    cache_url = httpserver2.url_for("/foo/bar")

    assert federation.cat("/foo/bar") == b"hello, world!"

    assert events[:3] == [
        ("pelicanfs.discover", (httpserver.url_for("/.well-known/pelican-configuration"),)),
        ("pelicanfs.token.prepare", (cache_url, "TokenRead")),
        ("pelicanfs.token.acquire", (cache_url, "TokenRead")),
    ]
    assert ("pelicanfs.read", ("/foo/bar", cache_url)) in events


@pytest.mark.usefixtures("generated_token")
def test_events_hide_authz_query(federation, events):
    federation.cat("/foo/bar?authz=secret")

    assert "pelicanfs.read" in [name for name, _ in events]
    assert "secret" not in repr(events)


@pytest.mark.usefixtures("generated_token")
def test_events_hide_token(federation, events):
    federation.cat("/foo/bar")
    federation.cat("/foo/bar")

    assert "gen-token" not in repr(events)


@pytest.mark.usefixtures("generated_token")
def test_open_event(federation, httpserver2: HTTPServer, events):
    federation.open("/foo/bar", "rb")

    assert ("pelicanfs.read", ("/foo/bar", httpserver2.url_for("/foo/bar"))) in events


@pytest.mark.usefixtures("generated_token")
def test_open_async_event(federation, httpserver2: HTTPServer, events):
    async def run():
        fp = await federation.open_async("/foo/bar")
        await fp.close()

    sync(federation.loop, run)

    assert ("pelicanfs.read", ("/foo/bar", httpserver2.url_for("/foo/bar"))) in events


def test_write_event(make_fs, httpserver: HTTPServer, tmp_path, events):
    url = httpserver.url_for("/foo/bar")
    httpserver.expect_request("/api/v1.0/director/origin/foo/bar").respond_with_data("", headers={"Location": url})
    httpserver.expect_request("/foo/bar", method="PUT").respond_with_data(status=200)
    local = tmp_path / "local"
    local.write_text("data")
    fs = make_fs()

    sync(get_loop(), fs._put_file, str(local), "/foo/bar")

    assert ("pelicanfs.write", ("/foo/bar", url)) in events


def test_list_event(make_fs, httpserver: HTTPServer, top_listing_response, events):
    url = httpserver.url_for("/foo/bar")
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{url}>; rel="collection"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={url}"},
    )
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response, status=207)
    fs = make_fs(f"namespace=/foo, collections-url={url}")

    fs.ls("/foo/bar", detail=False)

    assert ("pelicanfs.list", ("/foo/bar", url)) in events


@pytest.mark.usefixtures("generated_token")
def test_veto_stops_read_and_keeps_cache(federation, httpserver2: HTTPServer, events):
    global _veto  # noqa: PLW0603
    _veto = "pelicanfs.read"

    with pytest.raises(PermissionError):
        federation.cat("/foo/bar")

    assert "GET" not in [request.method for request, _ in httpserver2.log]
    assert federation.get_access_data().get_responses("/foo/bar") == ([], False)
    assert events[-1][0] == "pelicanfs.read"


@pytest.mark.parametrize("read", [lambda fs: fs.cat("/foo/bar"), lambda fs: fs.open("/foo/bar", "rb", cache_type="none").read()], ids=["cat", "open"])
def test_retry_raises_read_event_for_each_cache(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, read, events):
    httpserver2.clear()
    first, second = httpserver.url_for("/foo/bar"), httpserver2.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{first}>; rel="duplicate"; pri=1; depth=1, <{second}>; rel="duplicate"; pri=2; depth=1', "X-Pelican-Namespace": "namespace=/foo"},
    )
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=500)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_data("hello", status=206, headers={"Content-Range": "bytes 0-4/5"})
    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    assert read(pelfs) == b"hello"

    reads = [args for name, args in events if name == "pelicanfs.read"]
    assert reads == [("/foo/bar", first), ("/foo/bar", second)]


def test_veto_stops_token_acquire(federation, monkeypatch, events):
    global _veto  # noqa: PLW0603
    _veto = "pelicanfs.token.acquire"
    monkeypatch.setattr(TokenGenerator, "get_token", lambda _self: pytest.fail("token search ran"))

    with pytest.raises(PermissionError):
        federation.cat("/foo/bar")

    assert events[-1][0] == "pelicanfs.token.acquire"


@pytest.mark.usefixtures("generated_token")
@pytest.mark.parametrize("event", ["pelicanfs.discover", "pelicanfs.token.prepare", "pelicanfs.token.acquire"])
def test_veto_stops_cache_warming(federation, events, event):
    global _veto  # noqa: PLW0603
    _veto = event

    with pytest.raises(PermissionError, match=event):
        sync(federation.loop, federation._warm_namespace_cache, "/foo/bar")


@pytest.mark.usefixtures("generated_token")
@pytest.mark.parametrize("on_error", ["omit", "return"])
def test_cat_cannot_suppress_veto(federation, events, on_error):
    global _veto  # noqa: PLW0603
    _veto = "pelicanfs.read"

    with pytest.raises(PermissionError, match=r"pelicanfs\.read"):
        federation.cat(["/foo/bar"], on_error=on_error)


@pytest.fixture(name="tree")
def fixture_tree(make_fs, monkeypatch):
    fs = make_fs()
    listed = []

    async def resolve(path):
        return f"https://collections.example{path}", None

    async def listing(url, detail=True, client=None):
        listed.append(url)
        if url.endswith("/root"):
            return [{"name": f"{url}/{name}/", "type": "directory", "size": 0} for name in ["blocked", "allowed"]]
        return []

    monkeypatch.setattr(fs, "get_dirlist_url", resolve)
    monkeypatch.setattr(fs, "_ls_real", listing)
    return fs, listed


@pytest.mark.parametrize("method", ["walk", "fastwalk", "find", "du"])
def test_list_veto_stops_recursion(tree, events, method):
    global _veto, _veto_path  # noqa: PLW0603
    _veto = "pelicanfs.list"
    _veto_path = "/root/blocked"
    fs, listed = tree

    def run():
        result = getattr(fs, method)("/root")
        if method in {"walk", "fastwalk"}:
            list(result)

    with pytest.raises(PermissionError, match=r"pelicanfs\.list"):
        run()

    assert listed == ["https://collections.example/root"]


@pytest.mark.parametrize("method", ["walk", "fastwalk", "find"])
def test_one_list_event_per_directory(tree, events, method):
    fs, _listed = tree
    result = getattr(fs, method)("/root")
    if method in {"walk", "fastwalk"}:
        list(result)

    assert [args[0] for event, args in events if event == "pelicanfs.list"] == ["/root", "/root/blocked", "/root/allowed"]


@pytest.mark.parametrize("method", ["walk", "fastwalk", "find"])
def test_walk_can_still_omit_filesystem_errors(tree, monkeypatch, method):
    fs, listed = tree
    listing = fs._ls_real

    async def missing_child(url, **kwargs):
        if url.endswith("/blocked"):
            msg = "server denied access"
            raise PermissionError(msg)
        return await listing(url, **kwargs)

    monkeypatch.setattr(fs, "_ls_real", missing_child)
    result = getattr(fs, method)("/root")
    if method in {"walk", "fastwalk"}:
        list(result)

    assert listed == ["https://collections.example/root", "https://collections.example/root/allowed"]


def test_audited_walk_preserves_bottom_up_order(tree, events):
    fs, _listed = tree

    assert [path for path, _dirs, _files in fs.walk("/root", topdown=False)] == ["/root/blocked", "/root/allowed", "/root"]


@pytest.mark.usefixtures("generated_token")
def test_veto_does_not_affect_next_operation(federation, events):
    async def run():
        global _veto  # noqa: PLW0603
        _veto = "pelicanfs.read"
        with pytest.raises(PermissionError):
            await federation._cat("/foo/bar")
        _veto = None
        assert await federation._cat("/foo/bar") == b"hello, world!"

    sync(federation.loop, run)


@pytest.mark.usefixtures("generated_token")
def test_read_event_covers_open_file(federation, events):
    with federation.open("/foo/bar", "rb") as fp:
        assert fp.read(5) == b"hello"
        assert fp.read() == b", world!"

    assert len([event for event, _ in events if event == "pelicanfs.read"]) == 1
