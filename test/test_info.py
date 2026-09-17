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

import logging
import urllib.parse
from datetime import datetime

import aiohttp
import pytest
from pelican_webdav_client import InvalidResponseError, ResponseError
from pytest_httpserver import HTTPServer

import pelicanfs.core


def _multistatus(href, props="<D:getcontentlength>13</D:getcontentlength>"):
    """A PROPFIND reply describing the single resource at href."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>{href}</D:href>
    <D:propstat>
      <D:prop>{props}</D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""


def _setup_director(httpserver: HTTPServer, path="/foo/bar", collections_url=None):
    """
    Register httpserver as the director and the only cache for path, which may be
    percent-encoded. info() asks the director for the namespace's collections endpoint
    by PROPFIND, and for a cache by GET if it falls back to one.
    """
    namespace = "namespace=/foo" if collections_url is None else f"namespace=/foo, collections-url={collections_url}"
    headers = {"Link": f'<{httpserver.url_for(path)}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": namespace}
    server_path = urllib.parse.unquote(path)
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    for method in ("PROPFIND", "GET"):
        httpserver.expect_oneshot_request(server_path, method=method).respond_with_data("", status=307, headers=headers)
    httpserver.expect_request(server_path, method="HEAD").respond_with_data("hello, world!")


def _filesystem(httpserver: HTTPServer, get_client, get_webdav_client):
    return pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        get_webdav_client=get_webdav_client,
        skip_instance_cache=True,
    )


def test_info(httpserver: HTTPServer, get_client, get_webdav_client):
    """
    A namespace with no collections endpoint has its objects described by a cache.
    """
    props = "<D:getcontentlength>13</D:getcontentlength><D:getcontenttype>text/plain</D:getcontenttype><D:getlastmodified>Mon, 01 Jan 2024 00:00:00 GMT</D:getlastmodified>"
    _setup_director(httpserver)
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data(_multistatus("/foo/bar", props), status=207, content_type="application/xml")

    result = _filesystem(httpserver, get_client, get_webdav_client).info("/foo/bar")
    assert result["name"] == "/foo/bar"
    assert result["size"] == 13
    assert result["type"] == "file"
    assert result["mimetype"] == "text/plain"
    assert result["mtime"] == "Mon, 01 Jan 2024 00:00:00 GMT"
    # The same shape ls(detail=True) gives, with the modification time under both names
    assert result["modified"] == datetime(2024, 1, 1)
    assert set(result) == {"name", "type", "size", "mimetype", "modified", "mtime"}


def test_info_percent_encoded_path(httpserver: HTTPServer, get_client, get_webdav_client):
    """An encoded path reaches the server encoded once, not twice."""
    _setup_director(httpserver, "/foo/a%20b")
    httpserver.expect_request("/foo/a b", method="PROPFIND").respond_with_data(_multistatus("/foo/a%20b"), status=207, content_type="application/xml")

    assert _filesystem(httpserver, get_client, get_webdav_client).info("/foo/a%20b")["size"] == 13


def test_info_collection(httpserver: HTTPServer, get_client, get_webdav_client):
    """
    info() on a collection reports a directory, read from its resourcetype.

    An origin sends a collection's getcontentlength empty, which must not stop it being
    described: the size of a collection is 0.
    """
    props = "<D:getcontentlength></D:getcontentlength><D:getlastmodified>Mon, 01 Jan 2024 00:00:00 GMT</D:getlastmodified><D:resourcetype><D:collection/></D:resourcetype>"
    _setup_director(httpserver, collections_url=httpserver.url_for("/foo/bar"))
    # The origin, through its collections endpoint. No cache is asked: one would refuse
    # a PROPFIND on a collection.
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data(_multistatus("/foo/bar/", props), status=207, content_type="application/xml")

    result = _filesystem(httpserver, get_client, get_webdav_client).info("/foo/bar")
    assert result["name"] == "/foo/bar/"
    assert result["type"] == "directory"
    assert result["size"] == 0
    assert result["mtime"] == "Mon, 01 Jan 2024 00:00:00 GMT"


def test_info_falls_back_to_cache_when_origin_fails(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, get_webdav_client):
    """An object is still described by a cache while its origin is failing."""
    _setup_director(httpserver, collections_url=httpserver2.url_for("/"))
    httpserver2.expect_request("/foo/bar", method="PROPFIND").respond_with_data("unavailable", status=503)
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data(_multistatus("/foo/bar"), status=207, content_type="application/xml")

    result = _filesystem(httpserver, get_client, get_webdav_client).info("/foo/bar")
    assert result["type"] == "file"
    assert result["size"] == 13
    assert [request.method for request, _ in httpserver2.log] == ["PROPFIND"], "the origin was not asked first"


def test_info_origin_not_found_is_final(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, get_webdav_client):
    """A 404 from the origin is an answer, not a failure, so no cache is asked."""
    _setup_director(httpserver, collections_url=httpserver2.url_for("/"))
    httpserver2.expect_request("/foo/bar", method="PROPFIND").respond_with_data("not found", status=404)
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data(_multistatus("/foo/bar"), status=207, content_type="application/xml")

    with pytest.raises(FileNotFoundError):
        _filesystem(httpserver, get_client, get_webdav_client).info("/foo/bar")
    propfinds = [request for request, _ in httpserver.log if request.method == "PROPFIND"]
    assert len(propfinds) == 1, "a cache was asked after the origin's 404; only the director lookup should be a PROPFIND here"


def test_info_origin_failure_on_a_collection_is_reported(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, get_webdav_client):
    """
    When the origin fails and the cache then refuses the path as a collection, the
    origin's failure is what the caller sees, with the cache's refusal behind it.
    """
    _setup_director(httpserver, collections_url=httpserver2.url_for("/"))
    httpserver2.expect_request("/foo/bar", method="PROPFIND").respond_with_data("unavailable", status=503)
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data("Unable to locate /foo/bar; is a directory", status=409)

    with pytest.raises(ResponseError) as exc_info:
        _filesystem(httpserver, get_client, get_webdav_client).info("/foo/bar")
    assert exc_info.value.status == 503
    assert isinstance(exc_info.value.__cause__, ResponseError)
    assert exc_info.value.__cause__.status == 409


def test_info_unreachable_director_is_reported_once(httpserver: HTTPServer, get_client, get_webdav_client, caplog):
    """
    A director that cannot be reached is not an origin failure: info() reports it at
    once, instead of asking the same director again on the way to a cache.
    """
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": "https://127.0.0.1:1/"})

    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"), pytest.raises(aiohttp.ClientConnectorError):
        _filesystem(httpserver, get_client, get_webdav_client).info("/foo/bar")
    assert "asking the caches" not in caplog.text


def test_info_reply_that_is_not_a_multistatus_is_not_a_miss(httpserver: HTTPServer, httpserver2: HTTPServer, get_client, get_webdav_client):
    """A success that is not a multistatus says nothing about the path, so info() does not report it missing."""
    _setup_director(httpserver, collections_url=httpserver2.url_for("/"))
    httpserver2.expect_request("/foo/bar", method="PROPFIND").respond_with_data("<html>not a multistatus</html>", status=200)

    with pytest.raises(InvalidResponseError):
        _filesystem(httpserver, get_client, get_webdav_client).info("/foo/bar")


def test_info_semicolon_in_name(httpserver: HTTPServer, get_client, get_webdav_client):
    """A ";" in the last segment is part of the name, not the start of URL parameters."""
    _setup_director(httpserver, "/foo/a;b", collections_url=httpserver.url_for("/"))
    httpserver.expect_request("/foo/a;b", method="PROPFIND").respond_with_data(_multistatus("/foo/a%3Bb"), status=207, content_type="application/xml")

    assert _filesystem(httpserver, get_client, get_webdav_client).info("/foo/a;b")["size"] == 13


def test_info_is_logged_as_an_operation(httpserver: HTTPServer, get_client, get_webdav_client, caplog):
    """info() logs its start and finish under its own name, as other operations do."""
    _setup_director(httpserver, collections_url=httpserver.url_for("/foo/bar"))
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data(_multistatus("/foo/bar"), status=207, content_type="application/xml")

    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"):
        _filesystem(httpserver, get_client, get_webdav_client).info("/foo/bar")
    assert "Start _info /foo/bar" in caplog.text
    assert "_info finished in" in caplog.text


def test_du(
    httpserver: HTTPServer,
    get_client,
    get_webdav_client,
    top_listing_response,
    f1_listing_response,
    f2_listing_response,
    sf_listing_response,
    file1_listing_response,
    file2_listing_response,
    file3_listing_response,
    f1_file1_listing_response,
    f2_file1_listing_response,
    f2_file2_listing_response,
    sf_file_listing_response,
):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response)
    httpserver.expect_request("/foo/bar/folder1/", method="PROPFIND").respond_with_data(f1_listing_response)
    httpserver.expect_request("/foo/bar/folder2/", method="PROPFIND").respond_with_data(f2_listing_response)
    httpserver.expect_request("/foo/bar/folder1/subfolder1/", method="PROPFIND").respond_with_data(sf_listing_response)

    httpserver.expect_request("/foo/bar/file1.txt", method="PROPFIND").respond_with_data(
        file1_listing_response,
        status=207,
    )
    httpserver.expect_request("/foo/bar/file1.txt", method="HEAD").respond_with_data(
        "file1",
        status=200,
    )
    httpserver.expect_request("/foo/bar/file2.md", method="PROPFIND").respond_with_data(
        file2_listing_response,
        status=207,
    )
    httpserver.expect_request("/foo/bar/file2.md", method="HEAD").respond_with_data(
        "file2!!!!",
        status=200,
    )
    httpserver.expect_request("/foo/bar/file3.txt", method="PROPFIND").respond_with_data(
        file3_listing_response,
        status=207,
    )
    httpserver.expect_request("/foo/bar/file3.txt", method="HEAD").respond_with_data(
        "file3-with-extra-characters-for-more-content",
        status=200,
    )
    httpserver.expect_request("/foo/bar/folder1/file1.txt", method="PROPFIND").respond_with_data(
        f1_file1_listing_response,
        status=207,
    )
    httpserver.expect_request("/foo/bar/folder1/file1.txt", method="HEAD").respond_with_data(
        "folderfile1",
        status=200,
    )
    httpserver.expect_request("/foo/bar/folder1/subfolder1/file1.txt", method="PROPFIND").respond_with_data(
        sf_file_listing_response,
        status=207,
    )
    httpserver.expect_request("/foo/bar/folder1/subfolder1/file1.txt", method="HEAD").respond_with_data(
        "foldersubfolderfile1",
        status=200,
    )
    httpserver.expect_request("/foo/bar/folder2/file1.md", method="PROPFIND").respond_with_data(
        f2_file1_listing_response,
        status=207,
    )
    httpserver.expect_request("/foo/bar/folder2/file1.md", method="HEAD").respond_with_data(
        "file1-but-md-this-time",
        status=200,
    )
    httpserver.expect_request("/foo/bar/folder2/file2.md", method="PROPFIND").respond_with_data(
        f2_file2_listing_response,
        status=207,
    )
    httpserver.expect_request("/foo/bar/folder2/file2.md", method="HEAD").respond_with_data(
        "folder2file2-but-md-this-time",
        status=200,
    )

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert pelfs.du("/foo/bar") == 140


def test_isdir(
    httpserver: HTTPServer,
    get_client,
    get_webdav_client,
    top_listing_response,
    f1_listing_response,
    f2_listing_response,
    file1_listing_response,
    file2_listing_response,
    file3_listing_response,
):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response)
    httpserver.expect_request("/foo/bar/file1.txt", method="PROPFIND").respond_with_data(
        file1_listing_response,
        status=207,
    )
    httpserver.expect_request("/foo/bar/file3.txt", method="PROPFIND").respond_with_data(
        file3_listing_response,
        status=207,
    )
    httpserver.expect_request("/foo/bar/folder1/", method="PROPFIND").respond_with_data(f1_listing_response, status=207)
    httpserver.expect_request("/foo/bar/folder2/", method="PROPFIND").respond_with_data(f2_listing_response, status=207)
    httpserver.expect_request("/foo/bar/file2.md", method="PROPFIND").respond_with_data(
        file2_listing_response,
        status=207,
    )

    pelfs = pelicanfs.core.PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, get_webdav_client=get_webdav_client)

    assert pelfs.isdir("/foo/bar") is True


def test_isdir_file(httpserver: HTTPServer, get_client, get_webdav_client, file1_listing_response, not_a_collection_reply):
    body, status = not_a_collection_reply
    foo_bar_file_url = httpserver.url_for("foo/bar/file1.txt")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar/file1.txt").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_file_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_file_url}"},
    )

    httpserver.expect_request("/foo/bar/file1.txt/", method="PROPFIND").respond_with_data(body, status=status)

    httpserver.expect_request("/foo/bar/file1.txt", method="PROPFIND").respond_with_data(
        file1_listing_response,
        status=207,
    )

    pelfs = pelicanfs.core.PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, get_webdav_client=get_webdav_client)

    assert pelfs.isdir("/foo/bar/file1.txt") is False


def test_isdir_noexist(httpserver: HTTPServer, get_client, get_webdav_client):
    foo_bar_url = httpserver.url_for("foo/bar")

    httpserver.expect_request("/").respond_with_data("", status=200)
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}",
        },
    )
    httpserver.expect_oneshot_request("/foo/bar/", method="PROPFIND").respond_with_data(status=404)
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data(status=404)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert pelfs.isdir("/foo/bar") is False


def test_isdir_noexist_listing_response(httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response):
    """
    Test that a path is not a collection just because the origin answers its slash form with a listing.
    """
    new_file_url = httpserver.url_for("foo/bar/new.txt")

    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar/new.txt").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{new_file_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": f"namespace=/foo, collections-url={httpserver.url_for('/')}",
        },
    )
    # The origin answers the non-existent path's slash form with the parent's listing, and its
    # plain form with a 404
    httpserver.expect_request("/foo/bar/new.txt/", method="PROPFIND").respond_with_data(top_listing_response, status=207)
    httpserver.expect_request("/foo/bar/new.txt", method="PROPFIND").respond_with_data(status=404)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert pelfs.isdir("/foo/bar/new.txt") is False


def test_isfile(httpserver: HTTPServer, get_client, get_webdav_client, file1_listing_response):
    foo_bar_file_url = httpserver.url_for("foo/bar/file1.txt")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    httpserver.expect_oneshot_request("/foo/bar/file1.txt").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_file_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_file_url}",
        },
    )

    httpserver.expect_request("/foo/bar/file1.txt/", method="PROPFIND").respond_with_data(
        "not a directory",
        status=500,
    )

    httpserver.expect_request("/foo/bar/file1.txt", method="PROPFIND").respond_with_data(
        file1_listing_response,
        status=207,
    )

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert pelfs.isfile("/foo/bar/file1.txt") is True


def test_isfile_dir(httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/").respond_with_data("", status=200)
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )

    # A real origin answers a collection with or without the trailing slash, and the same
    # multistatus serves a depth-0 probe since the client only reads the entry it asked for
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data(top_listing_response, status=207)
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response, status=207)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert pelfs.isfile("/foo/bar") is False


def test_isfile_noexist(httpserver: HTTPServer, get_client, get_webdav_client):
    foo_bar_file2_url = httpserver.url_for("foo/bar/file2")

    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar/file2").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_file2_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_file2_url}"},
    )

    # A missing path is settled by the existence probe alone; nothing else is asked
    httpserver.expect_request("/foo/bar/file2", method="PROPFIND").respond_with_data(status=404)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert pelfs.isfile("/foo/bar/file2") is False
