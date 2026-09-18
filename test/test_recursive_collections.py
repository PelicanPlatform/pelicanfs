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

COLLECTIONS = [
    "/foo/bar",
    "/foo/bar/folder1",
    "/foo/bar/folder2",
    "/foo/bar/folder1/subfolder1",
]

FILES = [
    "/foo/bar/file1.txt",
    "/foo/bar/file2.md",
    "/foo/bar/file3.txt",
    "/foo/bar/folder1/file1.txt",
    "/foo/bar/folder1/subfolder1/file1.txt",
    "/foo/bar/folder2/file1.md",
    "/foo/bar/folder2/file2.md",
]


@pytest.fixture
def collection_tree_fs(
    httpserver: HTTPServer,
    get_client,
    get_webdav_client,
    top_listing_response,
    f1_listing_response,
    f2_listing_response,
    sf_listing_response,
):
    """
    A filesystem over a mock federation whose cache (and collections server) behaves like
    production: a HEAD or GET on a collection answers 409 Conflict, a GET on a file answers
    its content, and PROPFIND lists the collection tree /foo/bar -> {folder1/{subfolder1}, folder2}.
    """
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

    for collection in COLLECTIONS:
        for path in (collection, collection + "/"):
            httpserver.expect_request(path, method="HEAD").respond_with_data("", status=409)
            httpserver.expect_request(path, method="GET").respond_with_data("is a collection", status=409)
    for file in FILES:
        httpserver.expect_request(file, method="GET").respond_with_data(f"content of {file}")

    return pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )


def test_cat_recursive(collection_tree_fs):
    """
    cat(recursive=True) expands to the collection itself, its sub-collections and its files,
    exactly as fsspec does for a local directory. With the default on_error="raise" the result
    is IsADirectoryError (as on a local filesystem); with on_error="omit" the collections are
    dropped and every file is returned.
    """
    with pytest.raises(IsADirectoryError):
        collection_tree_fs.cat("/foo/bar", recursive=True)

    # A collection answer is not a cache fault, so the cache must still be usable afterwards
    assert collection_tree_fs.cat("/foo/bar", recursive=True, on_error="omit") == {file: f"content of {file}".encode() for file in FILES}


def test_find_withdirs(collection_tree_fs):
    """
    find(withdirs=True) has to report the collection root as a directory even though the
    server answers 409 to HEAD and GET on it.
    """
    assert collection_tree_fs.find("/foo/bar", withdirs=True) == [
        "/foo/bar",
        "/foo/bar/file1.txt",
        "/foo/bar/file2.md",
        "/foo/bar/file3.txt",
        "/foo/bar/folder1/",
        "/foo/bar/folder1/file1.txt",
        "/foo/bar/folder1/subfolder1/",
        "/foo/bar/folder1/subfolder1/file1.txt",
        "/foo/bar/folder2/",
        "/foo/bar/folder2/file1.md",
        "/foo/bar/folder2/file2.md",
    ]


def test_get_recursive(collection_tree_fs, tmp_path):
    """
    get(recursive=True) downloads the whole collection tree into the local directory,
    as fsspec does for any other filesystem.
    """
    collection_tree_fs.get("/foo/bar", str(tmp_path), recursive=True)

    downloaded = {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()}
    assert downloaded == {file.removeprefix("/foo/") for file in FILES}
    for file in FILES:
        assert (tmp_path / file.removeprefix("/foo/")).read_text() == f"content of {file}"
