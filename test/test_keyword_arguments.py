"""
Copyright (C) 2026, Pelican Project, Morgridge Institute for Research

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

OBJECT = "/ns/obj.txt"
CONTENTS = b"hello, world!"

# What an origin answers when asked about a single object: one response element, with a
# resourcetype that does not say "collection"
OBJECT_PROPFIND = f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>{OBJECT}</D:href>
    <D:propstat>
      <D:prop>
        <D:getcontentlength>{len(CONTENTS)}</D:getcontentlength>
        <D:getlastmodified>Tue, 25 Feb 2025 16:45:43 GMT</D:getlastmodified>
        <D:resourcetype/>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>
"""


@pytest.fixture(name="keyword_fs")
def fixture_keyword_fs(httpserver: HTTPServer, get_client, get_webdav_client):
    """A federation serving one object, reachable by every read method."""
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    # This one server plays director, cache and collections endpoint, so each method on
    # the object's path has to be registered separately -- a handler registered without
    # a method matches them all, including the PROPFINDs below
    director_headers = {
        "Link": f'<{httpserver.url_for(OBJECT)}>; rel="duplicate"; pri=1; depth=1',
        "X-Pelican-Namespace": f"namespace=/ns, collections-url={httpserver.url_for('/')}",
    }
    httpserver.expect_request(OBJECT, method="GET").respond_with_data(CONTENTS, status=200, headers=director_headers)
    # The liveness probe get_working_cache makes before settling on a cache
    httpserver.expect_request(OBJECT, method="HEAD").respond_with_data("", status=200, headers=director_headers)
    # An object rejects the collection form of a PROPFIND and answers the bare one. The
    # bare one carries the namespace headers too, because the director is asked for the
    # collections endpoint with a PROPFIND against this same path
    httpserver.expect_request(f"{OBJECT}/", method="PROPFIND").respond_with_data("not a collection", status=500)
    httpserver.expect_request(OBJECT, method="PROPFIND").respond_with_data(OBJECT_PROPFIND, status=207, headers=director_headers)

    return pelicanfs.core.PelicanFileSystem(
        httpserver.url_for("/"),
        get_client=get_client,
        skip_instance_cache=True,
        get_webdav_client=get_webdav_client,
    )


# Each entry is a read method and the keyword its path argument is named after, which is
# what fsspec's public signatures let a caller use.
@pytest.mark.parametrize(
    "method, keyword",
    [
        ("cat", "path"),
        ("cat_file", "path"),
        ("exists", "path"),
        ("info", "path"),
        ("ls", "path"),
        ("isdir", "path"),
    ],
)
def test_read_methods_accept_a_keyword_path(keyword_fs, method, keyword):
    """
    Passing the path by name gives the same answer as passing it positionally.

    The decorators that swap a namespace path for a cache or collections url used to
    reach for args[0], so any of these raised IndexError when called the way fsspec's
    signatures advertise -- fs.exists(path=...) rather than fs.exists(...).
    """
    call = getattr(keyword_fs, method)

    assert call(**{keyword: OBJECT}) == call(OBJECT)


def test_get_accepts_keyword_paths(keyword_fs, tmp_path):
    """get() names both of its paths, and callers do pass them by name."""
    dest = tmp_path / "obj.txt"

    keyword_fs.get(rpath=OBJECT, lpath=str(dest))

    assert dest.read_bytes() == CONTENTS


def test_keyword_path_leaves_the_other_arguments_in_place(keyword_fs):
    """Naming the path does not disturb the arguments that follow it."""
    assert keyword_fs.ls(path=OBJECT, detail=False) == keyword_fs.ls(OBJECT, detail=False)
    assert keyword_fs.ls(path=OBJECT, detail=True) == keyword_fs.ls(OBJECT, detail=True)


def test_missing_path_argument_reports_itself(keyword_fs):
    """Omitting the path entirely is a TypeError naming it, not an IndexError."""
    with pytest.raises(TypeError, match="path"):
        keyword_fs.exists()
