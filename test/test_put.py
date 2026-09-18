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
from aiohttp import ClientResponseError
from pytest_httpserver import HTTPServer

from pelicanfs.core import PelicanFileSystem
from pelicanfs.exceptions import NoAvailableSource


def test_put(httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response):
    foo_bar_url = httpserver.url_for("/foo/bar/test.py")
    base_url = httpserver.url_for("/")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": base_url})
    httpserver.expect_oneshot_request("/foo/bar/test.py").respond_with_data(
        "",
        status=307,
        headers={
            "Link": '<"some.other.url">; rel="duplicate"; pri=1; depth=1',
            "Location": foo_bar_url,
            "X-Pelican-Namespace": f"namespace=/foo, collections-url={base_url}",
        },
    )
    httpserver.expect_request("/foo/bar/test.py/", method="PROPFIND").respond_with_data("not a directory", status=500)
    httpserver.expect_request("/foo/bar/test.py", method="PROPFIND").respond_with_data(status=404)
    httpserver.expect_oneshot_request("/foo/bar/test.py", method="PUT").respond_with_data(status=200)
    httpserver.expect_request("/api/v1.0/director/origin/foo/bar/test.py").respond_with_data(
        "",
        status=200,
        headers={
            "Location": foo_bar_url,
        },
    )

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )
    pelfs.put(__file__, "/foo/bar/test.py")


def test_put_nonexistent_rpath_listing_response(httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response):
    """
    put() writes to the destination path itself when that path does not exist yet.
    """
    base_url = httpserver.url_for("/")
    new_file_url = httpserver.url_for("/foo/bar/new.txt")
    wrong_url = httpserver.url_for("/foo/bar/new.txt/test_put.py")

    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": base_url})
    httpserver.expect_oneshot_request("/foo/bar/new.txt").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{new_file_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": f"namespace=/foo, collections-url={base_url}",
        },
    )
    # The origin answers the non-existent path's slash form with the parent's listing, and its
    # plain form with a 404
    httpserver.expect_request("/foo/bar/new.txt/", method="PROPFIND").respond_with_data(top_listing_response, status=207)
    httpserver.expect_request("/foo/bar/new.txt", method="PROPFIND").respond_with_data(status=404)

    # Accept the upload at both the right and the wrong path, so a regression shows up as the
    # wrong path being written rather than as an unrelated error
    for path, url in (("/foo/bar/new.txt", new_file_url), ("/foo/bar/new.txt/test_put.py", wrong_url)):
        httpserver.expect_request(f"/api/v1.0/director/origin{path}").respond_with_data("", status=200, headers={"Location": url})
        httpserver.expect_request(path, method="PUT").respond_with_data(status=200)

    pelfs = PelicanFileSystem(
        base_url,
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )
    pelfs.put(__file__, "/foo/bar/new.txt")

    puts = [request.path for request, _ in httpserver.log if request.method == "PUT"]
    assert puts == ["/foo/bar/new.txt"]


def test_put_dest_dir(httpserver: HTTPServer, get_client, get_webdav_client, top_listing_response):
    foo_bar_url = httpserver.url_for("/foo/bar/")
    base_url = httpserver.url_for("/")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": base_url})
    httpserver.expect_oneshot_request("/foo/bar/", method="PUT").respond_with_data(status=200)
    httpserver.expect_request("/api/v1.0/director/origin/foo/bar/test_put.py").respond_with_data(
        "",
        status=200,
        headers={
            "Location": foo_bar_url,
        },
    )

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )
    pelfs.put(__file__, "/foo/bar/")


def test_put_recursive(httpserver: HTTPServer, get_client, get_webdav_client, tmp_path):
    """A recursive put of a local directory tree uploads every object in it.

    fsspec's _put calls _makedirs for each local directory before uploading the objects
    inside it. PelicanFS raises NotImplementedError for a direct makedirs() call, so this
    guards against that also breaking recursive uploads.
    """
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "sub" / "b.txt").write_text("b")
    remote_paths = [f"/foo/bar/{tmp_path.name}/a.txt", f"/foo/bar/{tmp_path.name}/sub/b.txt"]

    base_url = httpserver.url_for("/")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": base_url})
    for remote_path in remote_paths:
        httpserver.expect_request(f"/api/v1.0/director/origin{remote_path}").respond_with_data(
            "",
            status=200,
            headers={
                "Location": httpserver.url_for(remote_path),
            },
        )
        httpserver.expect_oneshot_request(remote_path, method="PUT").respond_with_data(status=200)

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )
    pelfs.put(str(tmp_path), "/foo/bar/", recursive=True)

    uploaded = sorted(request.path for request, _ in httpserver.log if request.method == "PUT")
    assert uploaded == sorted(remote_paths)


def test_put_no_available_source(httpserver: HTTPServer, get_client, get_webdav_client):
    base_url = httpserver.url_for("/")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": base_url})

    # Raise a 404 like a director would if the origin was not found
    httpserver.expect_oneshot_request("/foo/bar/test.py").respond_with_data(
        "",
        status=404,
    )

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )
    with pytest.raises(NoAvailableSource):
        pelfs.put(__file__, "/foo/bar/")


def test_put_permission_denied(httpserver: HTTPServer, get_client, get_webdav_client):
    foo_bar_url = httpserver.url_for("/foo/bar/")
    base_url = httpserver.url_for("/")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": base_url})
    httpserver.expect_oneshot_request("/foo/bar/", method="PUT").respond_with_data(status=403)
    httpserver.expect_request("/api/v1.0/director/origin/foo/bar/test_put.py").respond_with_data(
        "",
        status=200,
        headers={
            "Location": foo_bar_url,
        },
    )

    pelfs = PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )
    with pytest.raises(ClientResponseError):
        pelfs.put(__file__, "/foo/bar/")


def expect_origin_upload(httpserver: HTTPServer, put_status: int):
    """The Director points the upload at this server, and the Origin answers `put_status`."""
    foo_bar_url = httpserver.url_for("/foo/bar/test.py")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_request("/api/v1.0/director/origin/foo/bar/test.py").respond_with_data("", status=200, headers={"Location": foo_bar_url})
    httpserver.expect_request("/foo/bar/test.py", method="PUT").respond_with_data(status=put_status)


def expect_existence_probe(httpserver: HTTPServer, exists: bool):
    """What the existence check after a refused upload sees: Director, cache HEAD, then PROPFIND."""
    foo_bar_url = httpserver.url_for("/foo/bar/test.py")
    status = 200 if exists else 404
    httpserver.expect_request("/foo/bar/test.py", method="GET").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{foo_bar_url}>; rel="duplicate"; pri=1; depth=1',
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver.expect_request("/foo/bar/test.py", method="HEAD").respond_with_data("", status=status)
    httpserver.expect_request("/foo/bar/test.py", method="PROPFIND").respond_with_data("", status=status)


def make_pelfs(httpserver: HTTPServer, get_client, get_webdav_client):
    return PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )


def test_put_create_mode_uploads(httpserver: HTTPServer, get_client, get_webdav_client):
    """
    fsspec's mode="create" (exclusive create) is what every Pelican upload does anyway,
    so it is accepted rather than rejected with the http filesystem's "Exclusive write".
    """
    expect_origin_upload(httpserver, put_status=200)
    pelfs = make_pelfs(httpserver, get_client, get_webdav_client)

    pelfs.put_file(__file__, "/foo/bar/test.py", mode="create")


@pytest.mark.parametrize("mode", ["overwrite", "create"])
def test_put_existing_object_raises_file_exists(httpserver: HTTPServer, get_client, get_webdav_client, mode):
    """
    The Origin refuses to replace an existing object with a 403. When the object really
    is there, that refusal is reported as FileExistsError, in either mode, because
    "overwrite" never overwrites in Pelican.
    """
    expect_origin_upload(httpserver, put_status=403)
    expect_existence_probe(httpserver, exists=True)
    pelfs = make_pelfs(httpserver, get_client, get_webdav_client)

    with pytest.raises(FileExistsError):
        pelfs.put_file(__file__, "/foo/bar/test.py", mode=mode)


def test_put_forbidden_on_missing_object_keeps_http_error(httpserver: HTTPServer, get_client, get_webdav_client):
    """A 403 for an object that does not exist is a permission problem, not FileExistsError."""
    expect_origin_upload(httpserver, put_status=403)
    expect_existence_probe(httpserver, exists=False)
    pelfs = make_pelfs(httpserver, get_client, get_webdav_client)

    with pytest.raises(ClientResponseError):
        pelfs.put_file(__file__, "/foo/bar/test.py")


def test_put_rejects_unknown_mode(httpserver: HTTPServer, get_client, get_webdav_client):
    """An unsupported mode is rejected before any request is made."""
    pelfs = make_pelfs(httpserver, get_client, get_webdav_client)

    with pytest.raises(ValueError, match="append"):
        pelfs.put_file(__file__, "/foo/bar/test.py", mode="append")
    assert httpserver.log == []
