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

from datetime import UTC, datetime
from email.utils import format_datetime

import pytest
from fsspec.asyn import sync
from pelican_webdav_client import UnauthorizedError
from pytest_httpserver import HTTPServer

import pelicanfs.core
from pelicanfs.exceptions import NoCollectionsUrl
from pelicanfs.token_generator import TokenGenerator


def test_no_collections_url(httpserver: HTTPServer, get_client):
    foo_bar_url = httpserver.url_for("foo/bar")

    # Register the log_request and log_response functions
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": "namespace=/foo"},
    )

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
    )

    with pytest.raises(NoCollectionsUrl):
        pelfs.ls("/foo/bar")


def test_ls_dir(httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response):
    foo_bar_url = httpserver.url_for("foo/bar")

    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}",
        },
    )

    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response, status=207)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert pelfs.ls("/foo/bar", detail=False) == [
        "/foo/bar/file1.txt",
        "/foo/bar/file2.md",
        "/foo/bar/file3.txt",
        "/foo/bar/folder1/",
        "/foo/bar/folder2/",
    ]


def _listing_filesystem(httpserver: HTTPServer, get_client, get_webdav_client, listing):
    """A filesystem whose namespace /foo has its collections endpoint on httpserver, listing /foo/bar as listing."""
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}",
        },
    )
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(listing, status=207)
    return pelicanfs.core.PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, get_webdav_client=get_webdav_client)


def _listing_of(href):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/foo/bar/</D:href>
    <D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
  </D:response>
  <D:response>
    <D:href>{href}</D:href>
    <D:propstat><D:prop><D:getcontentlength>13</D:getcontentlength></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
  </D:response>
</D:multistatus>"""


@pytest.mark.parametrize(
    ("name", "listed"),
    [
        ("a%20b", "a%2520b"),
        ("a b", "a%20b"),
        ("a?b", "a%3Fb"),
        ("a#b", "a%23b"),
        ("a;b", "a%3Bb"),
        ("a+b:c=d", "a+b:c=d"),
    ],
)
def test_ls_name_reaches_the_listed_resource(httpserver: HTTPServer, get_client, get_webdav_client, name, listed):
    """A listed name is URL-encoded, so passing it back asks for the same resource."""
    pelfs = _listing_filesystem(httpserver, get_client, get_webdav_client, _listing_of(f"/foo/bar/{listed}"))
    path = f"/foo/bar/{listed}"

    assert pelfs.ls("/foo/bar", detail=False) == [path]
    assert [entry["name"] for entry in pelfs.ls("/foo/bar", detail=True)] == [path]

    # The namespace is known from the listing, so this goes straight to the collections endpoint
    multistatus = f"""<?xml version="1.0"?><D:multistatus xmlns:D="DAV:"><D:response><D:href>{path}</D:href>
<D:propstat><D:prop><D:getcontentlength>13</D:getcontentlength></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
</D:response></D:multistatus>"""
    httpserver.expect_request(f"/foo/bar/{name}", method="PROPFIND").respond_with_data(multistatus, status=207, content_type="application/xml")

    assert pelfs.info(path)["size"] == 13


def test_ls_detail(httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response):
    pelfs = _listing_filesystem(httpserver, get_client, get_webdav_client, top_listing_response)

    entries = {entry["name"]: entry for entry in pelfs.ls("/foo/bar", detail=True)}
    for entry in entries.values():
        # The same shape info() gives, with the modification time under both names
        assert set(entry) == {"name", "type", "size", "mimetype", "modified", "mtime"}
        assert isinstance(entry["size"], int)
        assert isinstance(entry["modified"], datetime)
        assert entry["modified"].tzinfo is None
        assert entry["mtime"] == format_datetime(entry["modified"].replace(tzinfo=UTC), usegmt=True)
    assert entries["/foo/bar/folder1/"]["type"] == "directory"
    assert entries["/foo/bar/folder1/"]["size"] == 0
    assert entries["/foo/bar/file1.txt"]["type"] == "file"


def test_ls_detail_collection_with_empty_size(httpserver: HTTPServer, get_client, get_webdav_client):
    """
    A detailed listing describes a sub-collection whose getcontentlength is empty, as an
    origin sends it, as a directory of size 0, and leaves out the listed collection.
    """
    propfind_response = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/foo/bar/</D:href>
    <D:propstat>
      <D:prop>
        <D:getcontentlength></D:getcontentlength>
        <D:resourcetype><D:collection/></D:resourcetype>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
  <D:response>
    <D:href>/foo/bar/sub/</D:href>
    <D:propstat>
      <D:prop>
        <D:getcontentlength></D:getcontentlength>
        <D:resourcetype><D:collection/></D:resourcetype>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
  <D:response>
    <D:href>/foo/bar/file1.txt</D:href>
    <D:propstat>
      <D:prop>
        <D:getcontentlength>13</D:getcontentlength>
        <D:resourcetype/>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""
    pelfs = _listing_filesystem(httpserver, get_client, get_webdav_client, propfind_response)

    listing = {entry["name"]: (entry["type"], entry["size"]) for entry in pelfs.ls("/foo/bar", detail=True)}
    assert listing == {
        "/foo/bar/sub/": ("directory", 0),
        "/foo/bar/file1.txt": ("file", 13),
    }


def test_ls_file(httpserver: HTTPServer, get_client, get_webdav_client, not_a_collection_reply):
    body, status = not_a_collection_reply
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

    httpserver.expect_oneshot_request("/foo/bar/", method="PROPFIND").respond_with_data(body, status=status)
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data("I'm a file!", status=200)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert pelfs.ls("/foo/bar", detail=False) == set()


def test_ls_nonexist(httpserver: HTTPServer, get_client, get_webdav_client):
    foo_bar_url = httpserver.url_for("foo/bar")

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

    with pytest.raises(FileNotFoundError):
        pelfs.ls("/foo/bar")


def test_glob_one_level(
    httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response, file1_listing_response, file2_listing_response, file3_listing_response, f1_listing_response, f2_listing_response
):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar/").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response, status=207)
    httpserver.expect_request("/foo/bar/file1.txt", method="PROPFIND").respond_with_data(file1_listing_response, status=207)
    httpserver.expect_request("/foo/bar/file2.md", method="PROPFIND").respond_with_data(file2_listing_response, status=207)
    httpserver.expect_request("/foo/bar/file3.txt", method="PROPFIND").respond_with_data(file3_listing_response, status=207)
    httpserver.expect_request("/foo/bar/folder1/", method="PROPFIND").respond_with_data(f1_listing_response, status=207)
    httpserver.expect_request("/foo/bar/folder2/", method="PROPFIND").respond_with_data(f2_listing_response, status=207)

    httpserver.expect_request("/foo/bar/", method="HEAD").respond_with_data(status=200)

    pelfs = pelicanfs.core.PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, get_webdav_client=get_webdav_client)

    assert pelfs.glob("/foo/bar/*") == ["/foo/bar/file1.txt", "/foo/bar/file2.md", "/foo/bar/file3.txt", "/foo/bar/folder1/", "/foo/bar/folder2/"]


def test_glob_pelican_url(
    httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response, file1_listing_response, file2_listing_response, file3_listing_response, f1_listing_response, f2_listing_response
):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar/").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response, status=207)
    httpserver.expect_request("/foo/bar/file1.txt", method="PROPFIND").respond_with_data(file1_listing_response, status=207)
    httpserver.expect_request("/foo/bar/file2.md", method="PROPFIND").respond_with_data(file2_listing_response, status=207)
    httpserver.expect_request("/foo/bar/file3.txt", method="PROPFIND").respond_with_data(file3_listing_response, status=207)
    httpserver.expect_request("/foo/bar/folder1/", method="PROPFIND").respond_with_data(f1_listing_response, status=207)
    httpserver.expect_request("/foo/bar/folder2/", method="PROPFIND").respond_with_data(f2_listing_response, status=207)

    httpserver.expect_request("/foo/bar/", method="HEAD").respond_with_data(status=200)

    host = httpserver.url_for("/").removeprefix("https://")
    pelfs = pelicanfs.core.PelicanFileSystem(f"pelican://{host}", get_client=get_client, skip_instance_cache=True, get_webdav_client=get_webdav_client)

    expected = ["/foo/bar/file1.txt", "/foo/bar/file2.md", "/foo/bar/file3.txt", "/foo/bar/folder1/", "/foo/bar/folder2/"]
    assert pelfs.glob(f"pelican://{host}foo/bar/*") == expected
    assert list(pelfs.glob(f"pelican://{host}foo/bar/*", detail=True)) == expected


def test_glob_match_ext(
    httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response, file1_listing_response, file2_listing_response, file3_listing_response, f1_listing_response, f2_listing_response
):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar/").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )

    httpserver.expect_request("/foo/bar/", method="HEAD").respond_with_data(status=200)
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response)
    httpserver.expect_request("/foo/bar/file1.txt", method="PROPFIND").respond_with_data(file1_listing_response)
    httpserver.expect_request("/foo/bar/file2.md", method="PROPFIND").respond_with_data(file2_listing_response)
    httpserver.expect_request("/foo/bar/file3.txt", method="PROPFIND").respond_with_data(file3_listing_response)
    httpserver.expect_request("/foo/bar/folder1/", method="PROPFIND").respond_with_data(f1_listing_response)
    httpserver.expect_request("/foo/bar/folder2/", method="PROPFIND").respond_with_data(f2_listing_response)

    pelfs = pelicanfs.core.PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, get_webdav_client=get_webdav_client)

    assert pelfs.glob("/foo/bar/file*.txt") == ["/foo/bar/file1.txt", "/foo/bar/file3.txt"]


def test_glob_match_one_level(
    httpserver: HTTPServer,
    get_client,
    get_webdav_client,
    top_listing_response,
    f1_listing_response,
    f2_listing_response,
    file1_listing_response,
    file2_listing_response,
    file3_listing_response,
    sf_listing_response,
    f1_file1_listing_response,
    f2_file2_listing_response,
    f2_file1_listing_response,
):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar/").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )

    httpserver.expect_request("/foo/bar/", method="HEAD").respond_with_data(status=200)
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response)
    httpserver.expect_request("/foo/bar/folder1/", method="PROPFIND").respond_with_data(f1_listing_response)
    httpserver.expect_request("/foo/bar/folder2/", method="PROPFIND").respond_with_data(f2_listing_response)
    httpserver.expect_request("/foo/bar/file1.txt", method="PROPFIND").respond_with_data(file1_listing_response)
    httpserver.expect_request("/foo/bar/file2.md", method="PROPFIND").respond_with_data(file2_listing_response)
    httpserver.expect_request("/foo/bar/file3.txt", method="PROPFIND").respond_with_data(file3_listing_response)
    httpserver.expect_request("/foo/bar/folder1/subfolder1/", method="PROPFIND").respond_with_data(sf_listing_response)
    httpserver.expect_request("/foo/bar/folder1/file1.txt", method="PROPFIND").respond_with_data(f1_file1_listing_response)
    httpserver.expect_request("/foo/bar/folder2/file2.md", method="PROPFIND").respond_with_data(f2_file2_listing_response)
    httpserver.expect_request("/foo/bar/folder2/file1.md", method="PROPFIND").respond_with_data(f2_file1_listing_response)

    pelfs = pelicanfs.core.PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, get_webdav_client=get_webdav_client)

    assert pelfs.glob("/foo/bar/*/file1.txt") == ["/foo/bar/folder1/file1.txt"]


def test_glob_match_multi_level(
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
    httpserver.expect_oneshot_request("/foo/bar/").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )

    httpserver.expect_request("/foo/bar/", method="HEAD").respond_with_data(status=200)
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response)
    httpserver.expect_request("/foo/bar/folder1/", method="PROPFIND").respond_with_data(f1_listing_response)
    httpserver.expect_request("/foo/bar/folder2/", method="PROPFIND").respond_with_data(f2_listing_response)
    httpserver.expect_request("/foo/bar/folder1/subfolder1/", method="PROPFIND").respond_with_data(sf_listing_response)
    httpserver.expect_request("/foo/bar/file1.txt", method="PROPFIND").respond_with_data(file1_listing_response)
    httpserver.expect_request("/foo/bar/file2.md", method="PROPFIND").respond_with_data(file2_listing_response)
    httpserver.expect_request("/foo/bar/file3.txt", method="PROPFIND").respond_with_data(file3_listing_response)
    httpserver.expect_request("/foo/bar/folder1/file1.txt", method="PROPFIND").respond_with_data(f1_file1_listing_response)
    httpserver.expect_request("/foo/bar/folder2/file1.md", method="PROPFIND").respond_with_data(f2_file1_listing_response)
    httpserver.expect_request("/foo/bar/folder2/file2.md", method="PROPFIND").respond_with_data(f2_file2_listing_response)
    httpserver.expect_request("/foo/bar/folder1/subfolder1/file1.txt", method="PROPFIND").respond_with_data(sf_file_listing_response)

    pelfs = pelicanfs.core.PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, get_webdav_client=get_webdav_client)

    assert pelfs.glob("/foo/bar/**/file1.*") == ["/foo/bar/file1.txt", "/foo/bar/folder1/file1.txt", "/foo/bar/folder1/subfolder1/file1.txt", "/foo/bar/folder2/file1.md"]


def test_find(
    httpserver: HTTPServer,
    get_client,
    get_webdav_client,
    top_listing_response,
    f1_listing_response,
    f2_listing_response,
    file1_listing_response,
    file2_listing_response,
    file3_listing_response,
    sf_listing_response,
    f1_file1_listing_response,
    f2_file2_listing_response,
    f2_file1_listing_response,
):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response)
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response)
    httpserver.expect_request("/foo/bar/folder1/", method="PROPFIND").respond_with_data(f1_listing_response)
    httpserver.expect_request("/foo/bar/folder2/", method="PROPFIND").respond_with_data(f2_listing_response)
    httpserver.expect_request("/foo/bar/folder1/subfolder1/", method="PROPFIND").respond_with_data(sf_listing_response)
    httpserver.expect_request("/foo/bar/file1.txt", method="PROPFIND").respond_with_data(file1_listing_response)
    httpserver.expect_request("/foo/bar/file2.md", method="PROPFIND").respond_with_data(file2_listing_response)
    httpserver.expect_request("/foo/bar/file3.txt", method="PROPFIND").respond_with_data(file3_listing_response)
    httpserver.expect_request("/foo/bar/folder1/file1.txt", method="PROPFIND").respond_with_data(f1_file1_listing_response)
    httpserver.expect_request("/foo/bar/folder2/file1.md", method="PROPFIND").respond_with_data(f2_file1_listing_response)
    httpserver.expect_request("/foo/bar/folder2/file2.md", method="PROPFIND").respond_with_data(f2_file2_listing_response)
    httpserver.expect_request("/foo/bar/folder1/subfolder1/file1.txt", method="PROPFIND").respond_with_data(sf_listing_response)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert pelfs.find("/foo/bar") == [
        "/foo/bar/file1.txt",
        "/foo/bar/file2.md",
        "/foo/bar/file3.txt",
        "/foo/bar/folder1/file1.txt",
        "/foo/bar/folder1/subfolder1/file1.txt",
        "/foo/bar/folder2/file1.md",
        "/foo/bar/folder2/file2.md",
    ]


@pytest.mark.parametrize("walk_impl", ["walk", "fastwalk"])
def test_walk(
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
    f2_file2_listing_response,
    f2_file1_listing_response,
    sf_file_listing_response,
    walk_impl,
):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )

    httpserver.expect_oneshot_request("/foo/bar/folder1").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )

    httpserver.expect_oneshot_request("/foo/bar/folder2").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )
    httpserver.expect_oneshot_request("/foo/bar/folder1/subfolder1").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )

    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response)
    httpserver.expect_request("/foo/bar/file1.txt", method="PROPFIND").respond_with_data(file1_listing_response)
    httpserver.expect_request("/foo/bar/file2.md", method="PROPFIND").respond_with_data(file2_listing_response)
    httpserver.expect_request("/foo/bar/file3.txt", method="PROPFIND").respond_with_data(file3_listing_response)

    httpserver.expect_request("/foo/bar/folder1/", method="PROPFIND").respond_with_data(f1_listing_response)
    httpserver.expect_request("/foo/bar/folder1/file1.txt", method="PROPFIND").respond_with_data(f1_file1_listing_response)

    httpserver.expect_request("/foo/bar/folder2/", method="PROPFIND").respond_with_data(f2_listing_response)
    httpserver.expect_request("/foo/bar/folder2/file1.md", method="PROPFIND").respond_with_data(f2_file1_listing_response)
    httpserver.expect_request("/foo/bar/folder2/file2.md", method="PROPFIND").respond_with_data(f2_file2_listing_response)

    httpserver.expect_request("/foo/bar/folder1/subfolder1/", method="PROPFIND").respond_with_data(sf_listing_response)
    httpserver.expect_request("/foo/bar/folder1/subfolder1/file1.txt", method="PROPFIND").respond_with_data(sf_file_listing_response)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    for sentinel, (root, dirnames, filenames) in enumerate(getattr(pelfs, walk_impl)("/foo/bar")):
        if sentinel == 0:
            assert root == "/foo/bar"
            assert dirnames == ["folder1", "folder2"]
            assert "file1.txt" in filenames
            assert "file2.md" in filenames
            assert "file3.txt" in filenames
            assert len(filenames) == 3
        elif sentinel == 1:
            assert root == "/foo/bar/folder1"
            assert dirnames == ["subfolder1"]
            assert "file1.txt" in filenames
            assert len(filenames) == 1
        elif sentinel == 2:
            assert root == "/foo/bar/folder1/subfolder1"
            assert dirnames == []
            assert "file1.txt" in filenames
            assert len(filenames) == 1
        elif sentinel == 3:
            assert root == "/foo/bar/folder2"
            assert dirnames == []
            assert "file1.md" in filenames
            assert "file2.md" in filenames
            assert len(filenames) == 2
        else:
            pytest.fail("Should not have reached this point, too many subdirectories")


def test_ls_from_http_awaits_token_generation(httpserver: HTTPServer, get_client, get_webdav_client, f2_listing_response, monkeypatch):
    """
    _ls_from_http has to await _handle_token_generation.

    Called without awaiting it builds a coroutine that is then discarded, so the token is
    never generated and never recorded on the filesystem. The listing that follows in the
    same call then goes out unauthenticated, which on a namespace that requires a token
    fails - with a RuntimeWarning as the only clue about why.

    The PROPFIND below is registered as only matching a request that carries the generated
    token, and anything else reaching that path is answered the way a server guarding it
    would answer, with a 401. So the server is what decides whether the token really was
    applied. Only the OIDC device flow is stubbed out; the director exchange,
    _handle_token_generation and the WebDAV listing are all the real thing.
    """
    collections_url = httpserver.url_for("/foo/bar")
    monkeypatch.setattr(TokenGenerator, "get_token", lambda _self: "generated-token")

    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{collections_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": f"namespace=/foo, collections-url={collections_url}, require-token=true",
        },
    )
    httpserver.expect_request(
        "/foo/bar/",
        method="PROPFIND",
        headers={"Authorization": "Bearer generated-token"},
    ).respond_with_data(f2_listing_response)
    # Registered second, so it only picks up what the matcher above turned down.
    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data("", status=401)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        get_webdav_client=get_webdav_client,
        skip_instance_cache=True,
    )
    assert pelfs.token is None, "this test only means something if the token has to be generated"

    # Driven through fsspec's loop, the way every other entry point into this filesystem
    # reaches it; asyncio.run would build a second loop and the client sessions are bound
    # to the first one.
    try:
        listing = sync(pelfs.loop, pelfs._ls_from_http, httpserver.url_for("/foo/bar"))
    except UnauthorizedError:
        pytest.fail("the PROPFIND went out with no token, so _handle_token_generation was never awaited")

    assert pelfs.token == "Bearer generated-token", "no token was recorded, so _handle_token_generation was never awaited"
    names = [entry["name"] for entry in listing]
    assert f"{collections_url}/folder2/file1.md" in names, "the token-gated PROPFIND did not match, so the listing went out unauthenticated"
    assert f"{collections_url}/folder2/file2.md" in names
