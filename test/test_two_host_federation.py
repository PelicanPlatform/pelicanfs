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

import pytest
from aiowebdav2.exceptions import ResponseErrorCodeError
from pytest_httpserver import HTTPServer

import pelicanfs.core

# The objects in the collection served by the listing fixtures, relative to /foo/bar
OBJECTS = (
    "file1.txt",
    "file2.md",
    "file3.txt",
    "folder1/file1.txt",
    "folder1/subfolder1/file1.txt",
    "folder2/file1.md",
    "folder2/file2.md",
)

COLLECTIONS = ("folder1", "folder1/subfolder1", "folder2")


def collection_multistatus(*hrefs):
    """A PROPFIND response listing each of `hrefs` as a collection."""
    entries = "".join(
        f"""
  <D:response>
    <D:href>{href}</D:href>
    <D:propstat>
      <D:prop>
        <D:getcontentlength>4096</D:getcontentlength>
        <D:getlastmodified>Tue, 25 Feb 2025 16:45:43 GMT</D:getlastmodified>
        <D:resourcetype>
          <D:collection/>
        </D:resourcetype>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>"""
        for href in hrefs
    )
    return f'<?xml version="1.0" encoding="utf-8"?>\n<D:multistatus xmlns:D="DAV:">{entries}\n</D:multistatus>\n'


# The namespace root, and a collection with nothing in it (a listing excludes the
# collection itself, so an empty one lists as just its own entry)
FOO_LISTING = collection_multistatus("/foo/", "/foo/bar/", "/foo/empty/")
EMPTY_LISTING = collection_multistatus("/foo/empty/")


@pytest.fixture(name="two_host_fs_factory")
def fixture_two_host_fs_factory(
    httpserver: HTTPServer,
    httpserver2: HTTPServer,
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
    """
    A federation whose cache and collections endpoint are two different hosts.

    `httpserver` plays the director and the cache; `httpserver2` plays the origin,
    which doubles as the collections endpoint. Keeping them apart is the whole point:
    pelicanfs lists from the collections endpoint but downloads from a cache, and a
    bug that mixes the two hosts is invisible when both are the same server.

    Returns a factory so tests can pass filesystem options (e.g. direct_reads=True).
    """
    httpserver2.clear()

    collections_url = httpserver2.url_for("/")
    namespace_header = f"namespace=/foo, collections-url={collections_url}"

    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    # The director answers for the collections (with or without a trailing slash) and
    # for every object under them, pointing each one at its own url on the cache
    for path in ("/foo", "/foo/", "/foo/bar", "/foo/bar/", "/foo/empty", "/foo/empty/", "/foo/broken", *(f"/foo/bar/{obj}" for obj in OBJECTS)):
        httpserver.expect_request(path).respond_with_data(
            f"contents of {path}",
            status=200,
            headers={
                "Link": f'<{httpserver.url_for(path)}>; rel="duplicate"; pri=1; depth=1',
                "X-Pelican-Namespace": namespace_header,
            },
        )

    # The director's origin api serves direct reads, pointing each object at the origin
    for path in ("/foo/bar", "/foo/bar/", *(f"/foo/bar/{obj}" for obj in OBJECTS)):
        httpserver.expect_request(f"/api/v1.0/director/origin{path}").respond_with_data(
            "",
            status=307,
            headers={
                "Link": f'<{httpserver2.url_for(path)}>; rel="duplicate"; pri=1; depth=1',
                "Location": httpserver2.url_for(path),
                "X-Pelican-Namespace": namespace_header,
            },
        )

    # The collections endpoint answers PROPFIND for the collections and their subtree.
    # Each collection is registered with and without a trailing slash, as a real
    # origin answers both forms; the same multistatus works for a full listing and a
    # depth-0 probe alike, since the client only reads the entry it asked for.
    collection_listings = {
        "/foo": FOO_LISTING,
        "/foo/bar": top_listing_response,
        "/foo/bar/folder1": f1_listing_response,
        "/foo/bar/folder2": f2_listing_response,
        "/foo/bar/folder1/subfolder1": sf_listing_response,
        "/foo/empty": EMPTY_LISTING,
    }
    for path, body in collection_listings.items():
        httpserver2.expect_request(path, method="PROPFIND").respond_with_data(body, status=207)
        httpserver2.expect_request(f"{path}/", method="PROPFIND").respond_with_data(body, status=207)

    object_listings = {
        "/foo/bar/file1.txt": file1_listing_response,
        "/foo/bar/file2.md": file2_listing_response,
        "/foo/bar/file3.txt": file3_listing_response,
        "/foo/bar/folder1/file1.txt": f1_file1_listing_response,
        "/foo/bar/folder1/subfolder1/file1.txt": sf_file_listing_response,
        "/foo/bar/folder2/file1.md": f2_file1_listing_response,
        "/foo/bar/folder2/file2.md": f2_file2_listing_response,
    }
    for path, body in object_listings.items():
        httpserver2.expect_request(path, method="PROPFIND").respond_with_data(body, status=207)

    # A PROPFIND for an object with a trailing slash is not a collection listing; the
    # origin rejects it, which is how _ls_real tells objects from collections
    for obj in OBJECTS:
        httpserver2.expect_request(f"/foo/bar/{obj}/", method="PROPFIND").respond_with_data("not a collection", status=500)

    # A path whose PROPFIND errors with something other than the object-signalling
    # 500 -- a genuinely failing collections endpoint
    for path in ("/foo/broken", "/foo/broken/"):
        httpserver2.expect_request(path, method="PROPFIND").respond_with_data("server having a bad day", status=503)

    # Direct reads fetch the objects from the origin; a distinct body proves provenance
    for obj in OBJECTS:
        httpserver2.expect_request(f"/foo/bar/{obj}", method="GET").respond_with_data(f"origin contents of /foo/bar/{obj}")

    # HEAD/GET for the collections themselves, which fsspec's _find probes on the root
    # of every walk -- including walks rooted at a subcollection, as a recursive glob does
    for path in ("/foo", "/foo/bar", "/foo/empty", *(f"/foo/bar/{c}" for c in COLLECTIONS)):
        httpserver2.expect_request(path).respond_with_data("", status=200)
        httpserver2.expect_request(f"{path}/").respond_with_data("", status=200)

    def make_fs(**kwargs):
        return pelicanfs.core.PelicanFileSystem(
            httpserver.url_for("/"),
            get_client=get_client,
            skip_instance_cache=True,
            get_webdav_client=get_webdav_client,
            **kwargs,
        )

    return make_fs


@pytest.fixture(name="two_host_federation")
def fixture_two_host_federation(two_host_fs_factory):
    return two_host_fs_factory()


def local_tree(root):
    """Every path under `root`, relative and posix-style, collections marked with a /."""
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            out.add(os.path.relpath(os.path.join(dirpath, name), root) + "/")
        for name in filenames:
            out.add(os.path.relpath(os.path.join(dirpath, name), root))
    return out


def object_gets(server):
    """The paths of every GET request `server` received for an object under /foo/bar."""
    return sorted(r.path for r, _ in server.log if r.method == "GET" and r.path.startswith("/foo/bar/"))


def test_get_recursive_lands_in_one_directory(two_host_federation, tmp_path):
    """
    A recursive get of a collection produces a single local directory.

    Regression test: the sources used to mix the cache's host (the root of the walk)
    with the collections endpoint's host (everything found underneath it). fsspec maps
    remote to local by common prefix, and across two hosts that prefix collapses to
    "https:", so every source was rewritten to "<lpath>/<host>/<namespace path>" -- the
    caller got two directories, one per host: an empty one holding just the collection
    root and a populated one holding the objects.
    """
    dest = tmp_path / "dest"
    dest.mkdir()

    two_host_federation.get("/foo/bar", str(dest), recursive=True)

    assert local_tree(dest) == {
        "bar/",
        *(f"bar/{c}/" for c in COLLECTIONS),
        *(f"bar/{obj}" for obj in OBJECTS),
    }
    assert (dest / "bar" / "folder1" / "subfolder1" / "file1.txt").read_text() == "contents of /foo/bar/folder1/subfolder1/file1.txt"


def test_get_recursive_trailing_slash_copies_contents(two_host_federation, tmp_path):
    """A trailing slash on the source copies the contents rather than the collection."""
    dest = tmp_path / "dest"
    dest.mkdir()

    two_host_federation.get("/foo/bar/", str(dest), recursive=True)

    assert local_tree(dest) == {
        *(f"{c}/" for c in COLLECTIONS),
        *OBJECTS,
    }


def test_get_recursive_downloads_from_the_cache(two_host_federation, httpserver: HTTPServer, httpserver2: HTTPServer, tmp_path):
    """
    The objects come from the cache, not from the collections endpoint.

    The two-host bug also meant every object in a recursive get was fetched straight
    from the origin's collections endpoint, because those were the urls the listing
    handed back.
    """
    dest = tmp_path / "dest"
    dest.mkdir()

    two_host_federation.get("/foo/bar", str(dest), recursive=True)

    assert object_gets(httpserver) == sorted(f"/foo/bar/{obj}" for obj in OBJECTS)
    assert object_gets(httpserver2) == []


def test_cat_recursive_reads_from_the_cache(two_host_federation, httpserver: HTTPServer, httpserver2: HTTPServer):
    """
    A recursive cat reads the objects from the cache, not the collections endpoint.

    Same defect as the recursive get: expansion went through the http filesystem, so
    every object was read straight off the origin's collections endpoint.
    """
    out = two_host_federation.cat("/foo/bar", recursive=True, on_error="omit")

    for obj in OBJECTS:
        assert out[f"/foo/bar/{obj}"] == f"contents of /foo/bar/{obj}".encode()

    collections_gets = [r.path for r, _ in httpserver2.log if r.method == "GET" and r.path.startswith("/foo/bar/")]
    assert collections_gets == []


def test_cat_single_object_returns_bytes(two_host_federation):
    """A single, non-recursive cat still returns bytes rather than a dict."""
    assert two_host_federation.cat("/foo/bar/file1.txt") == b"contents of /foo/bar/file1.txt"


def test_get_single_object(two_host_federation, tmp_path):
    """A non-recursive get of one object still works."""
    dest = tmp_path / "dest"
    dest.mkdir()

    two_host_federation.get("/foo/bar/file1.txt", str(dest))

    assert local_tree(dest) == {"file1.txt"}
    assert (dest / "file1.txt").read_text() == "contents of /foo/bar/file1.txt"


def test_get_recursive_empty_collection(two_host_federation, tmp_path):
    """
    A recursive get of an empty collection produces one empty local directory.

    Regression test: the root of a walk arrives without a trailing slash, and with no
    children to pre-create its local directory, _get_file used to fall through and
    download the collection url itself from the cache -- leaving a junk *file* where
    the empty directory should be.
    """
    dest = tmp_path / "dest"
    dest.mkdir()

    two_host_federation.get("/foo/empty", str(dest), recursive=True)

    assert local_tree(dest) == {"empty/"}


def test_get_recursive_glob_of_collections(two_host_federation, tmp_path):
    """
    A recursive get of a glob matching collections copies each matched tree.

    Glob results carry no trailing slash, so the matched collections reach _get_file
    looking like objects; they are recognized by their pre-created local directory and
    confirmed against the collections endpoint.
    """
    dest = tmp_path / "dest"
    dest.mkdir()

    two_host_federation.get("/foo/bar/folder*", str(dest), recursive=True)

    assert local_tree(dest) == {
        "folder1/",
        "folder1/file1.txt",
        "folder1/subfolder1/",
        "folder1/subfolder1/file1.txt",
        "folder2/",
        "folder2/file1.md",
        "folder2/file2.md",
    }


def test_get_recursive_glob_matching_empty_collection(two_host_federation, tmp_path):
    """
    A recursive get of a glob whose match is an *empty* collection creates the
    directory rather than downloading junk in its place.

    An empty match is the hardest case: it has no children to pre-create its local
    directory and no trailing slash, so its type has to come from the glob listing
    itself.
    """
    dest = tmp_path / "dest"
    dest.mkdir()

    two_host_federation.get("/foo/empt*", str(dest), recursive=True)

    assert local_tree(dest) == {"empty/"}


def test_get_stale_local_directory_fails_loudly(two_host_federation, tmp_path):
    """
    A remote *object* whose local destination is an existing directory is an error.

    The directory-skip in _get_file must not swallow real objects: stale local state
    used to make the download silently no-op.
    """
    dest = tmp_path / "dest"
    (dest / "bar" / "file1.txt").mkdir(parents=True)

    with pytest.raises(IsADirectoryError):
        two_host_federation.get("/foo/bar", str(dest), recursive=True)


def test_get_recursive_list_of_roots(two_host_federation, tmp_path):
    """
    A list of roots, gotten recursively, resolves each root's type independently.

    This is the path where the type probes run concurrently: one root here is an
    object and one is an empty collection, so both probe outcomes are exercised.
    """
    dest = tmp_path / "dest"
    dest.mkdir()

    two_host_federation.get(["/foo/bar/file1.txt", "/foo/empty"], str(dest), recursive=True)

    assert local_tree(dest) == {"file1.txt", "empty/"}
    assert (dest / "file1.txt").read_text() == "contents of /foo/bar/file1.txt"


def test_get_probe_server_error_propagates(two_host_federation, tmp_path):
    """
    A collections endpoint that errors on the type probe fails the get.

    Only the object-signalling 500 may be read as "not a collection"; a 503 must
    surface, not quietly reclassify a real collection and download junk in its place.
    """
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ResponseErrorCodeError):
        two_host_federation.get("/foo/broken", str(dest), recursive=True)

    assert local_tree(dest) == set()


def test_isdir_isfile(two_host_federation):
    """
    isdir/isfile answer by the resource's own type, not by its listing.

    The empty collection is the case that breaks listing-based answers: a webdav
    listing excludes the collection itself, so an empty one lists as nothing and used
    to read as a file.
    """
    assert two_host_federation.isdir("/foo/empty") is True
    assert two_host_federation.isfile("/foo/empty") is False

    assert two_host_federation.isdir("/foo/bar") is True
    assert two_host_federation.isfile("/foo/bar") is False

    assert two_host_federation.isdir("/foo/bar/file1.txt") is False
    assert two_host_federation.isfile("/foo/bar/file1.txt") is True


def test_get_nonrecursive_empty_collection_copies_nothing(two_host_federation, tmp_path):
    """
    A non-recursive get of a collection copies nothing, even when it is empty.

    fsspec filters directories out of a non-recursive get via isdir; an empty
    collection misread as a file used to slip through the filter and be downloaded
    as junk.
    """
    dest = tmp_path / "dest"
    dest.mkdir()

    two_host_federation.get("/foo/empty", str(dest))

    assert local_tree(dest) == set()


def test_get_recursive_direct_reads(two_host_fs_factory, httpserver: HTTPServer, tmp_path):
    """
    A recursive get with direct_reads=True fetches every object from the origin.

    The origin's object bodies are distinct from the cache's, so the contents prove
    where the bytes came from; the cache must see no object fetches at all.
    """
    pelfs = two_host_fs_factory(direct_reads=True)
    dest = tmp_path / "dest"
    dest.mkdir()

    pelfs.get("/foo/bar", str(dest), recursive=True)

    assert local_tree(dest) == {
        "bar/",
        *(f"bar/{c}/" for c in COLLECTIONS),
        *(f"bar/{obj}" for obj in OBJECTS),
    }
    assert (dest / "bar" / "file1.txt").read_text() == "origin contents of /foo/bar/file1.txt"
    assert object_gets(httpserver) == []
