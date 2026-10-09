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
#
# Fixtures shared by the whole test suite. They fall into three groups:
#
#   * TLS setup (ca, httpserver_listen_address, httpserver_ssl_context,
#     httpclient_ssl_context) -- pelican only speaks https, so the test servers have to
#     serve https too. These mint a throwaway certificate authority and hand the server
#     a certificate from it.
#   * Clients that trust that authority (get_client, get_webdav_client). A test passes
#     these to PelicanFileSystem, which calls them instead of building its own.
#   * Canned WebDAV listing bodies (*_listing_response), read from test/resources.
#
# Three of these fixtures are named by pytest-httpserver rather than by us --
# httpserver_listen_address, httpserver_ssl_context and httpclient_ssl_context. The
# plugin looks them up when it builds its own `httpserver` fixture, so overriding them
# here is what makes `httpserver` an https server the test clients can talk to.
#
import os
import ssl
from contextlib import asynccontextmanager

import aiohttp
import pytest
import trustme
from pytest_httpserver import HTTPServer

import pelicanfs.core


@pytest.fixture(scope="session", name="ca")
def fixture_ca():
    """
    A throwaway certificate authority, created fresh for each test session.

    Everything else in the TLS group derives from this one: the server gets a
    certificate signed by it, and the clients are told to trust it.
    """
    return trustme.CA()


@pytest.fixture(scope="session", name="httpserver_listen_address")
def fixture_httpserver_listen_address():
    """
    Where pytest-httpserver's `httpserver` should listen. Port 0 lets the OS pick a free
    one, so parallel runs and leftover sockets can't collide; use `url_for()` rather than
    hardcoding a port.
    """
    return ("127.0.0.1", 0)


@pytest.fixture(scope="session", name="httpserver_ssl_context")
def fixture_httpserver_ssl_context(ca):
    """
    The server side of the TLS setup: a context holding a certificate for 127.0.0.1
    signed by `ca`. pytest-httpserver picks this up automatically, which is what makes
    `httpserver` (and `httpserver2` below) serve https rather than http.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    localhost_cert = ca.issue_cert("localhost", "127.0.0.1")
    localhost_cert.configure_cert(context)
    return context


@pytest.fixture(scope="session", name="httpclient_ssl_context")
def fixture_httpclient_ssl_context(ca):
    """
    The client side of the TLS setup: a context that trusts `ca` and nothing unusual
    beyond it. Both client factories below build their connections from this, so requests
    to the test servers verify normally instead of needing verification turned off.
    """
    with ca.cert_pem.tempfile() as ca_temp_path:
        return ssl.create_default_context(cafile=ca_temp_path)


@pytest.fixture(scope="session", name="httpserver2")
def fixture_httpserver2(httpserver_listen_address, httpserver_ssl_context):
    """
    A second test server, on its own port, for tests that need two distinct hosts.

    A pelican federation splits roles across machines -- the director, the caches and the
    origin's collections endpoint are not the same host -- and a bug that confuses one
    for another is invisible when a single server plays every part. Tests that care about
    which host was contacted give each role its own server and read the two request logs
    separately (see test_two_host_federation.py and test_catalogue_reads.py).

    Unlike the plugin's own `httpserver`, this one is session-scoped and is not reset
    between tests, so call `httpserver2.clear()` before registering handlers on it.
    """
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


@pytest.fixture(scope="session", name="threaded_httpserver")
def fixture_threaded_httpserver(httpserver_listen_address, httpserver_ssl_context):
    """
    A server that serves connections in parallel rather than one at a time.

    The default server is single threaded, so a connection left open blocks the next
    request from being served at all. A test that checks connections are not being held
    open needs to make a second request while the first would still be held, and against
    the default server that request waits for aiohttp's connect timeout and fails on
    that instead of on the thing it set out to assert.
    """
    host, port = httpserver_listen_address
    if not host:
        host = HTTPServer.DEFAULT_LISTEN_HOST
    if not port:
        port = HTTPServer.DEFAULT_LISTEN_PORT

    server = HTTPServer(host=host, port=port, ssl_context=httpserver_ssl_context, threaded=True)
    server.start()
    yield server
    server.clear()
    if server.is_running():
        server.stop()


@pytest.fixture(scope="session", name="get_client")
def fixture_get_client(httpclient_ssl_context):
    """
    Builds the aiohttp session pelicanfs uses for ordinary http requests.

    Passed to PelicanFileSystem as `get_client=...`, which hands it to the fsspec
    HTTPFileSystem underneath; fsspec calls it when it needs a session. The only thing
    special about the session is that its connector trusts the test authority, so
    requests to the test servers don't fail certificate verification.
    """

    async def client_factory(**kwargs):
        connector = aiohttp.TCPConnector(ssl=httpclient_ssl_context)
        return aiohttp.ClientSession(connector=connector, **kwargs)

    return client_factory


@pytest.fixture(scope="session", name="get_webdav_client")
def fixture_get_webdav_client(httpclient_ssl_context):
    """
    Builds the WebDAV client pelicanfs uses for listings.

    The equivalent of `get_client` for the other half of the library: listings are
    PROPFIND requests against the collections endpoint rather than plain GETs, so they go
    through aiowebdav2 instead of aiohttp. Passed to PelicanFileSystem as
    `get_webdav_client=...` and used wherever it needs to know whether a path is an
    object or a collection, or what a collection contains.

    This deliberately delegates to the production get_webdav_client, pointed at the test
    authority, so that the tests exercise the real client setup - including the release
    of the responses it produces - instead of a copy of it that can drift.
    """

    @asynccontextmanager
    async def client_factory(options, **kwargs):
        async with pelicanfs.core.get_webdav_client({**options, "verify_ssl": httpclient_ssl_context}) as client:
            yield client

    return client_factory


#
# Canned PROPFIND responses, read verbatim from test/resources. A test registers one as
# the reply to a PROPFIND so the library sees a realistic WebDAV answer without a real
# origin behind it.
#
# They all describe one tree, rooted at /foo/bar:
#
#     /foo/bar/
#     |-- file1.txt
#     |-- file2.md
#     |-- file3.txt
#     |-- folder1/
#     |   |-- file1.txt
#     |   `-- subfolder1/
#     |       `-- file1.txt
#     `-- folder2/
#         |-- file1.md
#         `-- file2.md
#
# Two kinds, distinguished by the name:
#
#   * A collection listing -- top, f1, f2, sf -- answers a PROPFIND for a directory and
#     names the directory itself followed by its children. This is what a listing call
#     consumes.
#   * A single-resource response -- everything else -- answers a PROPFIND for one object
#     and names only that object. This is what an existence or type check consumes.
#
# The abbreviations are f1/f2 for folder1/folder2 and sf for subfolder1, so e.g.
# f2_file1 is the response for the single object /foo/bar/folder2/file1.md.
#


@pytest.fixture
def top_listing_response():
    """Listing of /foo/bar/: three objects plus folder1 and folder2."""
    file_path = os.path.join(os.path.dirname(__file__), "resources", "top_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def f1_listing_response():
    """Listing of /foo/bar/folder1/: file1.txt plus subfolder1."""
    file_path = os.path.join(os.path.dirname(__file__), "resources", "f1_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def f2_listing_response():
    """Listing of /foo/bar/folder2/: file1.md and file2.md."""
    file_path = os.path.join(os.path.dirname(__file__), "resources", "f2_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def sf_listing_response():
    """Listing of /foo/bar/folder1/subfolder1/: one object, file1.txt."""
    file_path = os.path.join(os.path.dirname(__file__), "resources", "sf_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def file1_listing_response():
    """The single object /foo/bar/file1.txt."""
    file_path = os.path.join(os.path.dirname(__file__), "resources", "file1_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def file2_listing_response():
    """The single object /foo/bar/file2.md."""
    file_path = os.path.join(os.path.dirname(__file__), "resources", "file2_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def file3_listing_response():
    """The single object /foo/bar/file3.txt."""
    file_path = os.path.join(os.path.dirname(__file__), "resources", "file3_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def f1_file1_listing_response():
    """The single object /foo/bar/folder1/file1.txt."""
    file_path = os.path.join(os.path.dirname(__file__), "resources", "f1_file1_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def sf_file_listing_response():
    """The single object /foo/bar/folder1/subfolder1/file1.txt."""
    file_path = os.path.join(os.path.dirname(__file__), "resources", "sf_file_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def f2_file1_listing_response():
    """The single object /foo/bar/folder2/file1.md."""
    file_path = os.path.join(os.path.dirname(__file__), "resources", "f2_file1_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def f2_file2_listing_response():
    """The single object /foo/bar/folder2/file2.md."""
    file_path = os.path.join(os.path.dirname(__file__), "resources", "f2_file2_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()
