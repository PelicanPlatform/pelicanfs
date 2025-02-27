"""
Copyright (C) 2024, Pelican Project, Morgridge Institute for Research

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

import os
import ssl

import aiohttp
import pytest
import trustme
from aiowebdav.client import Client
from pytest_httpserver import HTTPServer

import pelicanfs.core
from pelicanfs.core import NoAvailableSource, PelicanFileSystem


@pytest.fixture(scope="session", name="ca")
def fixture_ca():
    return trustme.CA()


@pytest.fixture(scope="session", name="httpserver_listen_address")
def fixture_httpserver_listen_address():
    return ("localhost", 0)


@pytest.fixture(scope="session", name="httpserver_ssl_context")
def fixture_httpserver_ssl_context(ca):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    localhost_cert = ca.issue_cert("localhost")
    localhost_cert.configure_cert(context)
    return context


@pytest.fixture(scope="session", name="httpclient_ssl_context")
def fixture_httpclient_ssl_context(ca):
    with ca.cert_pem.tempfile() as ca_temp_path:
        return ssl.create_default_context(cafile=ca_temp_path)


@pytest.fixture(scope="session", name="httpserver2")
def fixture_httpserver2(httpserver_listen_address, httpserver_ssl_context):
    host, port = httpserver_listen_address
    if not host:
        host = HTTPServer.DEFAULT_LISTEN_HOST
    if not port:
        port = HTTPServer.DEFAULT_LISTEN_PORT

    server = HTTPServer(host=host, port=port, ssl_context=httpserver_ssl_context)
    server.start()
    yield server
    server.clear()
    if server.is_running():
        server.stop()


@pytest.fixture(scope="session", name="get_client")
def fixture_get_client(httpclient_ssl_context):
    async def client_factory(**kwargs):
        connector = aiohttp.TCPConnector(ssl=httpclient_ssl_context)
        return aiohttp.ClientSession(connector=connector, **kwargs)

    return client_factory


@pytest.fixture(scope="session", name="get_webdav_client")
def fixture_get_webdav_client(httpclient_ssl_context):
    def client_factory(options, **kwargs):
        connector = aiohttp.TCPConnector(ssl=httpclient_ssl_context)
        client = Client(options)
        session = aiohttp.ClientSession(connector=connector, **kwargs)
        client.session = session
        return client

    return client_factory


@pytest.fixture
def listing_response():
    file_path = os.path.join(os.path.dirname(__file__), "resources", "xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


def test_ls(httpserver: HTTPServer, get_client, get_webdav_client, listing_response):
    foo_bar_url = httpserver.url_for("foo/bar")

    # Register the log_request and log_response functions

    httpserver.expect_request("/").respond_with_data("", status=200)
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver.expect_oneshot_request("/foo/bar/", method="HEAD").respond_with_data(listing_response)
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(listing_response)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert pelfs.ls("/foo/bar", detail=False) == [
        "/foo/bar/file1",
        "/foo/bar/file2",
        "/foo/bar/file3",
    ]


def test_glob(httpserver: HTTPServer, get_client, get_webdav_client, listing_response):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar/*").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver.expect_oneshot_request("/foo/bar/").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )

    httpserver.expect_request("/foo/bar/", method="HEAD").respond_with_data(listing_response)
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(listing_response)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data(listing_response)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data(listing_response)

    pelfs = pelicanfs.core.PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, get_webdav_client=get_webdav_client)

    assert pelfs.glob("/foo/bar/*") == ["/foo/bar/file1", "/foo/bar/file2", "/foo/bar/file3"]


def test_find(httpserver: HTTPServer, get_client, get_webdav_client, listing_response):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data(listing_response)
    httpserver.expect_request("/foo/bar/", method="HEAD").respond_with_data()
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(listing_response)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert pelfs.find("/foo/bar") == ["/foo/bar/file1", "/foo/bar/file2", "/foo/bar/file3"]


def test_info(httpserver: HTTPServer, get_client, listing_response):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello, world!")
    # httpserver.expect_request("/foo/bar", method="GET").respond_with_data(listing_response)
    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    assert pelfs.info("/foo/bar") == {
        "name": "/foo/bar",
        "size": 13,
        "mimetype": "text/plain",
        "url": "/foo/bar",
        "type": "file",
    }


def test_du(httpserver: HTTPServer, get_client, get_webdav_client, listing_response):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver.expect_request("/foo/bar/", method="HEAD").respond_with_data("")
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(listing_response)
    httpserver.expect_request("/foo/bar/file1", method="HEAD").respond_with_data(
        "file1",
        status=307,
    )
    httpserver.expect_request("/foo/bar/file2", method="HEAD").respond_with_data(
        "file2!!!!",
        status=307,
    )
    httpserver.expect_request("/foo/bar/file3", method="HEAD").respond_with_data(
        "file3-with-extra-characters-for-more-content",
        status=307,
    )

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert pelfs.du("/foo/bar") == 58


def test_isdir(httpserver: HTTPServer, get_client, get_webdav_client, listing_response):
    foo_bar_url = httpserver.url_for("foo/bar")
    foo_bar_file_url = httpserver.url_for("foo/bar/file1")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver.expect_request("/foo/bar/", method="HEAD").respond_with_data("")
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(listing_response)
    httpserver.expect_oneshot_request("/foo/bar/file1").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_file_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver.expect_request("/foo/bar/file1", method="GET").respond_with_data(
        "file1",
        status=307,
    )

    pelfs = pelicanfs.core.PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, get_webdav_client=get_webdav_client)

    assert pelfs.isdir("/foo/bar") is True
    assert pelfs.isdir("/foo/bar/file1") is False


def test_isfile(httpserver: HTTPServer, get_client, get_webdav_client, listing_response):
    foo_bar_url = httpserver.url_for("foo/bar")
    foo_bar_file_url = httpserver.url_for("foo/bar/file1")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver.expect_oneshot_request("/foo/bar/file1").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_file_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver.expect_request("/foo/bar/", method="HEAD").respond_with_data("")
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(listing_response)
    httpserver.expect_request("/foo/bar/file1", method="HEAD").respond_with_data("file1")

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert not pelfs.isfile("/foo/bar")
    assert pelfs.isfile("/foo/bar/file1")


def test_walk(httpserver: HTTPServer, get_client, get_webdav_client, listing_response):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(listing_response)
    httpserver.expect_request("/foo/bar/", method="HEAD").respond_with_data("")

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    for root, dirnames, filenames in pelfs.walk("/foo/bar"):
        assert root == "/foo/bar"
        assert dirnames == []
        assert "file1" in filenames
        assert "file2" in filenames
        assert "file3" in filenames
        assert len(filenames) == 3


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
