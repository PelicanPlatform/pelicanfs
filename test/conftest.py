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
import os
import ssl
from contextlib import asynccontextmanager

import aiohttp
import pytest
import trustme
from aiowebdav2.client import Client, ClientOptions
from pytest_httpserver import HTTPServer


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
    @asynccontextmanager
    async def client_factory(options, **kwargs):
        # Extract options
        base_url = options.get("hostname")
        token = options.get("token")

        # Create a custom TCPConnector with SSL context (if needed)
        connector = aiohttp.TCPConnector(ssl=httpclient_ssl_context)

        # Create a custom session with the connector
        session = aiohttp.ClientSession(connector=connector, **kwargs)

        # Initialize aiowebdav2 Client with username and password (empty for token auth)
        client = Client(
            url=base_url,
            username="",  # Empty username for token-based auth
            password="",  # Empty password for token-based auth
        )

        # Create a new ClientOptions object by passing the current options
        # We don't have direct access to all attributes, so let's fetch what we need directly
        new_options = ClientOptions(
            verify_ssl=httpclient_ssl_context,  # Apply the custom SSL context
            timeout=options.get("timeout", None),  # Use provided timeout or None
            proxy=options.get("proxy", None),  # Use provided proxy or None
            proxy_auth=options.get("proxy_auth", None),  # Use provided proxy_auth or None
            token=token,  # Use the token from options
        )

        # Assign the new options object to the client
        client._options = new_options

        # Set the Authorization header directly on the session
        session.headers["Authorization"] = f"Bearer {token}"

        # Close internal client session
        original_session = client._session
        if not original_session.closed:
            await original_session.close()

        # Assign the custom session to the client
        client._session = session
        client._session_created = True  # Mark that session is manually created

        try:
            yield client
        finally:
            await client._session.close()

    return client_factory


@pytest.fixture
def top_listing_response():
    file_path = os.path.join(os.path.dirname(__file__), "resources", "top_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def f1_listing_response():
    file_path = os.path.join(os.path.dirname(__file__), "resources", "f1_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def f2_listing_response():
    file_path = os.path.join(os.path.dirname(__file__), "resources", "f2_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def sf_listing_response():
    file_path = os.path.join(os.path.dirname(__file__), "resources", "sf_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def file1_listing_response():
    file_path = os.path.join(os.path.dirname(__file__), "resources", "file1_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def file2_listing_response():
    file_path = os.path.join(os.path.dirname(__file__), "resources", "file2_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def file3_listing_response():
    file_path = os.path.join(os.path.dirname(__file__), "resources", "file3_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def f1_file1_listing_response():
    file_path = os.path.join(os.path.dirname(__file__), "resources", "f1_file1_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def sf_file_listing_response():
    file_path = os.path.join(os.path.dirname(__file__), "resources", "sf_file_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def f2_file1_listing_response():
    file_path = os.path.join(os.path.dirname(__file__), "resources", "f2_file1_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()


@pytest.fixture
def f2_file2_listing_response():
    file_path = os.path.join(os.path.dirname(__file__), "resources", "f2_file2_xml_response.xml")
    with open(file_path, "r") as f:
        return f.read()
