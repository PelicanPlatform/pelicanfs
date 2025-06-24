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
from pytest_httpserver import HTTPServer

import pelicanfs.core
from pelicanfs.exceptions import NoCollectionsUrl


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


def test_ls_file(httpserver: HTTPServer, get_client, get_webdav_client):
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

    httpserver.expect_oneshot_request("/foo/bar/", method="PROPFIND").respond_with_data("not a directory", status=500)
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

    sentinel = 0
    for root, dirnames, filenames in pelfs.walk("/foo/bar"):
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
            assert False, "Should not have reached this point, too many subdirectories"

        sentinel += 1
