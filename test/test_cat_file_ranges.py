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

Tests that cat_file treats start and end as slice bounds.
"""

import itertools
import re

import aiohttp
import pytest
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Request, Response

from pelicanfs.core import PelicanFileSystem

DATA = b"Hello, World!"
CACHE_PROBE = "HEAD"
BOUNDS = [None, -20, -14, -13, -5, -1, 0, 1, 5, 12, 13, 14, 20]


def serve_ranges(request: Request) -> Response:
    """Answer a Range header as an origin does, with a 416 for what it can't satisfy."""
    header = request.headers.get("Range")
    if request.method == "HEAD" or header is None:
        return Response(DATA)
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if match is None:
        return Response(status=416)
    first, last = match.groups()
    if not first:
        return Response(DATA[-int(last) :], status=206)
    last = int(last) if last else len(DATA) - 1
    if int(first) >= len(DATA) or last < int(first):
        return Response(status=416)
    return Response(DATA[int(first) : last + 1], status=206)


@pytest.fixture(name="pelfs")
def fixture_pelfs(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    httpserver2.clear()
    cache_url = httpserver2.url_for("/foo/bar")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_request("/foo/bar").respond_with_data(
        "",
        status=307,
        headers={
            "Link": f'<{cache_url}>; rel="duplicate"; pri=1; depth=1',
            "Location": cache_url,
            "X-Pelican-Namespace": "namespace=/foo",
        },
    )
    httpserver2.expect_request("/foo/bar").respond_with_handler(serve_ranges)
    return PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)


def is_past_end(start: int | None, end: int | None) -> bool:
    return start is not None and start >= len(DATA) and (end is None or end > start)


@pytest.mark.parametrize(("start", "end"), [pair for pair in itertools.product(BOUNDS, BOUNDS) if not is_past_end(*pair)])
def test_bounds_match_a_slice(pelfs, start, end):
    assert pelfs.cat_file("/foo/bar", start, end) == DATA[start:end]


@pytest.mark.parametrize(("start", "end"), [pair for pair in itertools.product(BOUNDS, BOUNDS) if is_past_end(*pair)])
def test_range_past_the_end_still_raises(pelfs, start, end):
    with pytest.raises(aiohttp.ClientResponseError) as excinfo:
        pelfs.cat_file("/foo/bar", start, end)
    assert excinfo.value.status == 416


@pytest.mark.parametrize(("start", "end"), [(5, 1), (None, 0), (5, 5)])
def test_empty_range_reads_nothing(pelfs, httpserver2, start, end):
    assert pelfs.cat_file("/foo/bar", start, end) == b""
    assert [request.method for request, _ in httpserver2.log] == [CACHE_PROBE]


def test_negative_bounds_look_up_the_size_once(pelfs, httpserver2):
    assert pelfs.cat_file("/foo/bar", -20, -5) == DATA[-20:-5]
    assert [request.method for request, _ in httpserver2.log] == [CACHE_PROBE, "HEAD", "GET"]
