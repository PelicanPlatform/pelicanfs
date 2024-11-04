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

import ssl
import aiohttp
import pytest
from pytest_httpserver import HTTPServer
import trustme

import pelicanfs.core
from pelicanfs.core import PelicanFileSystem, NoAvailableSource



LISTING_RESPONSE = ('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" \
                    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">\n'
                    '<html xmlns="http://www.w3.org/1999/xhtml">\n<head>\n\
                        <meta http-equiv="content-type" content="text/html;charset=utf-8"/>\n'
                    '<link rel="stylesheet" type="text/css" href="/static/css/xrdhttp.css"/>\n\
                        <link rel="icon" type="image/png" href="/static/icons/xrdhttp.ico"/>\n'
                    '<title>/foo/bar</title>\n</head>\n<body>\n<h1>'
                    'Listing of: /foo/bar</h1>\n'
                    '<div id="header"><table id="ft">\n<thead><tr>\n\
                        <th class="mode">Mode</th>\
                            <th class="flags">Flags</th><th class="size">Size</th>'
                    '<th class="datetime">Modified</th><th \
                        class="name">Name</th></tr></thead>\n\
                            <tr><td class="mode">---r--</td><td class="mode">16</td>'
                    '<td class="size">24</td>\
                        <td class="datetime">Wed, 20 Mar 2024 15:50:39 GMT</td>'
                    '<td class="name"><a href="/foo/bar/file1">file1</a></td></tr>'
                    '<tr><td class="mode">---r--</td><td class="mode">16</td>\
                        <td class="size">1116</td>\
                            <td class="datetime">Wed, 20 Mar 2024 15:50:40 GMT</td>'
                    '<td class="name"><a href="/foo/bar/file2">file2/a></td></tr>'
                    '<tr><td class="mode">d--r-x</td><td class="mode">19</td>\
                        <td class="size">4096</td>\
                            <td class="datetime">Wed, 20 Mar 2024 15:50:40 GMT</td>'
                    '<td class="name"><a href="/foo/bar/file3">file3</a></td></tr>'
                    '</table></div><br><br>\
                        <hr size=1><p><span id="requestby">Request by \
                            unknown.189071:38@[::ffff:128.104.153.58] \
                                ( [::ffff:128.104.153.58] )</span></p>\n\
                                    <p>Powered by XrdHTTP v5.6.8 (CERN IT-SDC)</p>\n')

@pytest.fixture(scope="session", name="ca")
def fixture_ca():
    """
    Create CA for use in testing
    """
    return trustme.CA()

@pytest.fixture(scope="session", name="httpserver_listen_address")
def fixture_httpserver_listen_address():
    """
    Use the localhost address for the httpserver testing
    """
    return ("localhost", 0)

@pytest.fixture(scope="session", name="httpserver_ssl_context")
def fixture_httpserver_ssl_context(ca):
    """
    Create a SSLContext for using in testing
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    localhost_cert = ca.issue_cert("localhost")
    localhost_cert.configure_cert(context)
    return context

@pytest.fixture(scope="session", name="httpclient_ssl_context")
def fixture_httpclient_ssl_context(ca):
    """
    Create an httpclient for testing with our temp CAs
    """
    with ca.cert_pem.tempfile() as ca_temp_path:
        return ssl.create_default_context(cafile=ca_temp_path)

@pytest.fixture(scope="session", name="httpserver2")
def fixture_httpserver2(httpserver_listen_address, httpserver_ssl_context):
    """
    Create a second httpserver for testing
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

@pytest.fixture(scope="session", name="get_client")
def fixture_get_client(httpclient_ssl_context):
    """
    Create and return an http client for testing 
    """
    async def client_factory(**kwargs):
        connector = aiohttp.TCPConnector(ssl=httpclient_ssl_context)
        return aiohttp.ClientSession(connector=connector, **kwargs)

    return client_factory

def test_ls(httpserver: HTTPServer, get_client):
    """
    Test the LS function in pelicanfs
    """
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration") \
        .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data(LISTING_RESPONSE)
    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    assert pelfs.ls("/foo/bar", detail=False) == ['/foo/bar/file1',
                                                  '/foo/bar/file2',
                                                  '/foo/bar/file3']

def test_glob(httpserver: HTTPServer, get_client):
    """
    Test the glob function with pelicanfs
    """
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration")\
        .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar/*").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver.expect_oneshot_request("/foo/bar/").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data(LISTING_RESPONSE)
    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    assert pelfs.glob("/foo/bar/*") == ['/foo/bar/file1', '/foo/bar/file2', '/foo/bar/file3']

def test_find(httpserver: HTTPServer, get_client):
    """
    Test the find function for pelicanfs
    """
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration")\
        .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data(LISTING_RESPONSE)
    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    assert pelfs.find("/foo/bar") == ['/foo/bar/file1', '/foo/bar/file2', '/foo/bar/file3']

def test_info(httpserver: HTTPServer, get_client):
    """
    Test the info function for pelicanfs
    """
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration")\
        .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data(LISTING_RESPONSE)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data(LISTING_RESPONSE)
    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    assert pelfs.info("/foo/bar") == {'name': '/foo/bar',
                                      'size': 1845, 'mimetype': 'text/plain',
                                      'url': '/foo/bar', 'type': 'file'}

def test_du(httpserver: HTTPServer, get_client):
    """
    Test the du function for pelicanfs
    """
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration")\
    .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data(LISTING_RESPONSE)
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
    )

    assert pelfs.du("/foo/bar") == 58


def test_isdir(httpserver: HTTPServer, get_client):
    """
    Test the isdir function for pelicanfs
    """
    foo_bar_url = httpserver.url_for("foo/bar")
    foo_bar_file_url = httpserver.url_for("foo/bar/file1")
    httpserver.expect_request("/.well-known/pelican-configuration").\
        respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver.expect_request("/foo/bar", method="GET")\
        .respond_with_data(LISTING_RESPONSE)
    httpserver.expect_oneshot_request("/foo/bar/file1").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_file_url}>; rel="duplicate"; pri=1; depth=1',
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver.expect_request("/foo/bar/file1", method="GET").respond_with_data(
        "file1",
        status=307,
        )

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    assert pelfs.isdir("/foo/bar") is True
    assert pelfs.isdir("/foo/bar/file1") is False

def test_isfile(httpserver: HTTPServer, get_client):
    """
    Test the isfile function for pelicanfs
    """
    foo_bar_url = httpserver.url_for("foo/bar")
    foo_bar_file_url = httpserver.url_for("foo/bar/file1")
    httpserver.expect_request("/.well-known/pelican-configuration")\
        .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data(LISTING_RESPONSE)
    httpserver.expect_oneshot_request("/foo/bar/file1").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_file_url}>; rel="duplicate"; pri=1; depth=1',
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver.expect_request("/foo/bar/file1", method="GET").respond_with_data(
        "file1",
        status=307,
        )

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    assert pelfs.isfile("/foo/bar") is False
    assert pelfs.isfile("/foo/bar/file1") is True


def test_walk(httpserver: HTTPServer, get_client):
    """
    Test the walk function for pelicanfs
    """
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration")\
        .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data(LISTING_RESPONSE)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    for root, dirnames, filenames in pelfs.walk("/foo/bar"):
        assert root == "/foo/bar"
        assert dirnames == []
        assert 'file1' in filenames
        assert 'file2' in filenames
        assert 'file3' in filenames
        assert len(filenames) == 3

def test_open(httpserver: HTTPServer, get_client):
    """
    Test the open function for pelicanfs
    """
    foo_bar_url = httpserver.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration")\
    .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "Location": foo_bar_url,
                 "X-Pelican-Namespace": "namespace=/foo"
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
    """
    Test multiple servers for pelicanfs
    """
    foo_bar_url = httpserver2.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration")\
        .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "Location": foo_bar_url,
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver2.expect_oneshot_request("/foo/bar", method="HEAD")\
        .respond_with_data("hello, world 2")
    httpserver2.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("hello, world 2")

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )
    assert pelfs.cat("/foo/bar") == b"hello, world 2"

def test_open_fallback(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """
    Test that a request with no available server returns the correct error
    """
    foo_bar_url = httpserver.url_for("/foo/bar")
    foo_bar_url2 = httpserver2.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration")\
        .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1, '
                         f'<{foo_bar_url2}>; rel="duplicate"; pri=2; depth=1',
                 "Location": foo_bar_url,
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver2.expect_oneshot_request("/foo/bar", method="HEAD")\
        .respond_with_data("hello, world 2")
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

def test_open_preferred(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """
    Test pelicanfs with a preferred cache
    """
    foo_bar_url = httpserver.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration")\
        .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "Location": foo_bar_url,
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver2.expect_oneshot_request("/foo/bar", method="HEAD")\
        .respond_with_data("hello, world")
    httpserver2.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("hello, world")

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        preferred_caches=[httpserver2.url_for("/")],
    )
    assert pelfs.cat("/foo/bar") == b"hello, world"

def test_open_preferred_plus(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """
    Test pelicanfs with a preferred class with a '+'
    """
    foo_bar_url = httpserver.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration")\
        .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "Location": foo_bar_url,
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver2.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data("hello, world")
    httpserver2.expect_oneshot_request("/foo/bar", method="GET")\
        .respond_with_data("hello, world", status=500)
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
    """
    Test the PelicanMap function
    """
    foo_url = httpserver.url_for("/foo")
    foo_bar_url = httpserver.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration")\
        .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_url}>; rel="duplicate"; pri=1; depth=1',
                 "Location": foo_url,
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )
    httpserver.expect_request("/foo", method="HEAD").respond_with_data("hello, world!")

    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "Location": foo_bar_url,
                 "X-Pelican-Namespace": "namespace=/foo"
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
    assert pel_map['bar'] == b'hello, world!'

def test_authorization_headers(httpserver: HTTPServer, get_client):
    """
    Test authorization headers with tokens for pelicanfs
    """
    foo_bar_url = httpserver.url_for("/foo/bar")
    test_headers_with_bearer = {"Authorization": "Bearer test"}

    httpserver.expect_request("/.well-known/pelican-configuration")\
        .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "Location": foo_bar_url,
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )

    httpserver.expect_request("/foo/bar", headers=test_headers_with_bearer, method="HEAD")\
        .respond_with_data("hello, world!")
    httpserver.expect_request("/foo/bar", headers=test_headers_with_bearer, method="GET")\
        .respond_with_data("hello, world!")

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        headers = test_headers_with_bearer
    )

    assert pelfs.cat("/foo/bar", headers={'Authorization': 'Bearer test'}) == b"hello, world!"

def test_authz_query(httpserver: HTTPServer, get_client):
    """
    Test passing tokens with authz in the query header
    """
    foo_bar_url = httpserver.url_for("/foo/bar")

    httpserver.expect_request("/.well-known/pelican-configuration")\
        .respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
                 "Location": foo_bar_url,
                 "X-Pelican-Namespace": "namespace=/foo"
                },
        )

    httpserver.expect_request("/foo/bar", query_string="authz=test", method="HEAD")\
        .respond_with_data("hello, world!")
    httpserver.expect_request("/foo/bar", query_string="authz=test", method="GET")\
        .respond_with_data("hello, world!")

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    assert pelfs.cat("/foo/bar?authz=test") == b"hello, world!"
