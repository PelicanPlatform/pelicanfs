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
from pytest_httpserver import HTTPServer

import pelicanfs.core


def test_info(httpserver: HTTPServer, get_client):
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


def test_isdir_file(httpserver: HTTPServer, get_client, get_webdav_client, file1_listing_response):
    foo_bar_file_url = httpserver.url_for("foo/bar/file1.txt")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar/file1.txt").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_file_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_file_url}"},
    )

    httpserver.expect_request("/foo/bar/file1.txt/", method="PROPFIND").respond_with_data(
        "not a directory",
        status=500,
    )

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


def test_isfile_dir(
    httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response, file1_listing_response, file2_listing_response, file3_listing_response, f1_listing_response, f2_listing_response
):
    foo_bar_url = httpserver.url_for("foo/bar")
    httpserver.expect_request("/").respond_with_data("", status=200)
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1', "X-Pelican-Namespace": f"namespace=/foo, collections-url={foo_bar_url}"},
    )

    httpserver.expect_request("/foo/bar/", method="PROPFIND").respond_with_data(top_listing_response, status=207)

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

    httpserver.expect_request("/foo/bar/file2/", method="PROPFIND").respond_with_data(status=404)
    httpserver.expect_request("/foo/bar/file2", method="PROPFIND").respond_with_data(status=404)

    pelfs = pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )

    assert pelfs.isfile("/foo/bar/file2") is False
