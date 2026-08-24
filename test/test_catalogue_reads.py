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

# A catalogue-driven read (intake -> xarray -> zarr) asks for a store's chunks in one
# bulk call, so these tests care as much about how many times the director is consulted
# as they do about the bytes coming back.
STORE = "/chtc/PUBLIC/ncar/monthly/cesm2LE-FLNS.zarr"
CHUNKS = (".zmetadata", ".zgroup", "FLNS/.zarray", *(f"FLNS/{i}.0.0" for i in range(16)))


@pytest.fixture(name="store_fs_factory")
def fixture_store_fs_factory(httpserver: HTTPServer, get_client):
    """
    A single-host federation serving a zarr store's chunks.

    Returns a factory so tests can pass filesystem options (e.g. direct_reads=True).
    """
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    for chunk in CHUNKS:
        path = f"{STORE}/{chunk}"
        httpserver.expect_request(path).respond_with_data(
            f"bytes of {chunk}",
            status=200,
            headers={
                "Link": f'<{httpserver.url_for(path)}>; rel="duplicate"; pri=1; depth=1',
                "X-Pelican-Namespace": "namespace=/chtc",
            },
        )
        # The origin api serves direct reads for the same objects
        httpserver.expect_request(f"/api/v1.0/director/origin{path}").respond_with_data(
            "",
            status=307,
            headers={
                "Link": f'<{httpserver.url_for(path)}>; rel="duplicate"; pri=1; depth=1',
                "Location": httpserver.url_for(path),
                "X-Pelican-Namespace": "namespace=/chtc",
            },
        )

    def make_fs(**kwargs):
        return pelicanfs.core.PelicanFileSystem(
            httpserver.url_for("/"),
            get_client=get_client,
            skip_instance_cache=True,
            **kwargs,
        )

    return make_fs


@pytest.fixture(name="store_federation")
def fixture_store_federation(store_fs_factory):
    return store_fs_factory()


def director_calls(httpserver, total_objects):
    """Requests to the director, i.e. GETs that were not object fetches or discovery."""
    gets = [r.path for r, _ in httpserver.log if r.method == "GET"]
    discovery = [p for p in gets if p.endswith("pelican-configuration")]
    return len(gets) - total_objects - len(discovery)


def test_cat_list_consults_the_director_once(store_federation, httpserver: HTTPServer):
    """
    A bulk cat resolves one cache for the namespace, not one per object.

    The objects are fetched concurrently, so unless the namespace cache is warm before
    the fan-out every one of them races off to the director for the same answer -- which
    for a real zarr store means thousands of redundant director round trips.
    """
    keys = [f"{STORE}/{chunk}" for chunk in CHUNKS]

    out = store_federation.cat(keys)

    assert out == {f"{STORE}/{chunk}": f"bytes of {chunk}".encode() for chunk in CHUNKS}
    assert director_calls(httpserver, len(keys)) == 1


def test_cat_list_direct_reads(store_fs_factory, httpserver: HTTPServer):
    """
    The same bulk read with direct_reads=True fetches every object from its origin.

    Origins are resolved per object -- there is no namespace cache for them -- so this
    also documents the current one-origin-lookup-per-object behavior.
    """
    pelfs = store_fs_factory(direct_reads=True)
    keys = [f"{STORE}/{chunk}" for chunk in CHUNKS]

    out = pelfs.cat(keys)

    assert out == {f"{STORE}/{chunk}": f"bytes of {chunk}".encode() for chunk in CHUNKS}
    origin_lookups = [r.path for r, _ in httpserver.log if r.path.startswith("/api/v1.0/director/origin/")]
    assert len(origin_lookups) == len(keys)


def test_mapper_getitems(store_federation, httpserver: HTTPServer):
    """The same bulk read through PelicanMap, which is how a catalogue reaches a store."""
    mapper = pelicanfs.core.PelicanMap(STORE, pelfs=store_federation)

    out = mapper.getitems(list(CHUNKS))

    assert out == {chunk: f"bytes of {chunk}".encode() for chunk in CHUNKS}
    assert director_calls(httpserver, len(CHUNKS)) == 1


def test_mapper_single_key(store_federation):
    """A single key through the mapper still returns its bytes."""
    mapper = pelicanfs.core.PelicanMap(STORE, pelfs=store_federation)

    assert mapper[".zmetadata"] == b"bytes of .zmetadata"


def test_cat_osdf_urls(httpserver: HTTPServer, get_client):
    """
    Catalogue entries are full osdf:// urls, and cat keys its results by namespace path.

    The intake catalogue in examples/intake stores paths like
    osdf:///chtc/PUBLIC/.../foo.zarr, so a bulk read arrives as a list of urls rather
    than of bare paths.
    """
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    for chunk in (".zgroup", "FLNS/0.0.0"):
        path = f"{STORE}/{chunk}"
        httpserver.expect_request(path).respond_with_data(
            f"bytes of {chunk}",
            status=200,
            headers={
                "Link": f'<{httpserver.url_for(path)}>; rel="duplicate"; pri=1; depth=1',
                "X-Pelican-Namespace": "namespace=/chtc",
            },
        )

    pelfs = pelicanfs.core.PelicanFileSystem(
        "pelican://osg-htc.org/",
        get_client=get_client,
        skip_instance_cache=True,
    )
    # osdf:// pins federation discovery to the real osg-htc.org; setting the director
    # directly skips discovery and keeps the test off the network
    pelfs.director_url = httpserver.url_for("/")

    out = pelfs.cat([f"osdf://{STORE}/.zgroup", f"osdf://{STORE}/FLNS/0.0.0"])

    assert out == {
        f"{STORE}/.zgroup": b"bytes of .zgroup",
        f"{STORE}/FLNS/0.0.0": b"bytes of FLNS/0.0.0",
    }
