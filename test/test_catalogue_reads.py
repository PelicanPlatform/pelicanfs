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

# These tests cover the access pattern used by scientific data catalogues, where the
# library is driven by another tool rather than called directly. A catalogue (intake) is
# an index of datasets; opening one hands xarray a zarr store, which is directory of small objects
#
# See examples/intake for a catalogue whose entries are osdf:// urls of stores like this.

# One store, laid out the way zarr does it:
#   .zmetadata     consolidated metadata for the whole store
#   .zgroup        marks the store as a group of arrays
#   FLNS/.zarray   the FLNS array's shape, dtype and chunking
#   FLNS/<i>.0.0   the array's data, one object per chunk
STORE = "/chtc/PUBLIC/ncar/monthly/cesm2LE-FLNS.zarr"
CHUNKS = (".zmetadata", ".zgroup", "FLNS/.zarray", *(f"FLNS/{i}.0.0" for i in range(16)))


@pytest.fixture(name="store_fs_factory")
def fixture_store_fs_factory(httpserver: HTTPServer, httpserver2: HTTPServer, get_client):
    """
    A federation serving one zarr store, with the director and the cache on separate hosts.

    `httpserver` is the director, `httpserver2` is the cache (and, for direct reads, the
    origin). They have to be different hosts for the tests below to be able to count
    director traffic: the director is asked for an object under the same path the object
    is then fetched from, so on a single host the two are indistinguishable in the log.

    Returns a factory so tests can pass filesystem options (e.g. direct_reads=True).
    """
    httpserver2.clear()

    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})

    for chunk in CHUNKS:
        path = f"{STORE}/{chunk}"
        cache_url = httpserver2.url_for(path)

        # The director points at the cache holding the object
        httpserver.expect_request(path).respond_with_data(
            "",
            status=307,
            headers={
                "Link": f'<{cache_url}>; rel="duplicate"; pri=1; depth=1',
                "X-Pelican-Namespace": "namespace=/chtc",
            },
        )
        # The director's origin endpoint, which direct reads use instead: it redirects
        # to the origin rather than returning the object, and Location is what is followed
        httpserver.expect_request(f"/api/v1.0/director/origin{path}").respond_with_data(
            "",
            status=307,
            headers={
                "Link": f'<{cache_url}>; rel="duplicate"; pri=1; depth=1',
                "Location": cache_url,
                "X-Pelican-Namespace": "namespace=/chtc",
            },
        )
        # The cache (or origin) serves the bytes
        httpserver2.expect_request(path).respond_with_data(f"bytes of {chunk}", status=200)

    def make_fs(**kwargs):
        return pelicanfs.core.PelicanFileSystem(
            httpserver.url_for("/"),
            get_client=get_client,
            # skip_instance_cache is consumed by fsspec's caching metaclass, not by
            # __init__; it stops these filesystems being shared between tests
            skip_instance_cache=True,
            **kwargs,
        )

    return make_fs


@pytest.fixture(name="store_federation")
def fixture_store_federation(store_fs_factory):
    return store_fs_factory()


def director_lookups(httpserver):
    """The paths the director was asked to resolve, ignoring federation discovery."""
    return [r.path for r, _ in httpserver.log if r.method == "GET" and not r.path.endswith("pelican-configuration")]


def test_cat_list_consults_the_director_once(store_federation, httpserver: HTTPServer, httpserver2: HTTPServer):
    """
    A bulk cat resolves one cache for the whole namespace, not one per object.

    The objects are fetched concurrently, so unless a cache has been resolved before the
    fan-out every one of them races off to the director for the same answer -- for a real
    store, thousands of redundant round trips.
    """
    keys = [f"{STORE}/{chunk}" for chunk in CHUNKS]

    out = store_federation.cat(keys)

    assert out == {f"{STORE}/{chunk}": f"bytes of {chunk}".encode() for chunk in CHUNKS}
    assert len(director_lookups(httpserver)) == 1
    # every object still came back, and came from the cache (which also sees one HEAD,
    # the liveness probe get_working_cache makes before settling on it)
    cache_gets = [r.path for r, _ in httpserver2.log if r.method == "GET"]
    assert sorted(cache_gets) == sorted(keys)


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
    """
    The same bulk read through PelicanMap.

    A mapper is how a catalogue actually reaches a store: zarr is handed something that
    behaves like a dict of keys, and asks for many of them at once through getitems.
    """
    mapper = pelicanfs.core.PelicanMap(STORE, pelfs=store_federation)

    out = mapper.getitems(list(CHUNKS))

    assert out == {chunk: f"bytes of {chunk}".encode() for chunk in CHUNKS}
    assert len(director_lookups(httpserver)) == 1


def test_mapper_single_key(store_federation):
    """A single key through the mapper still returns its bytes."""
    mapper = pelicanfs.core.PelicanMap(STORE, pelfs=store_federation)

    assert mapper[".zmetadata"] == b"bytes of .zmetadata"


def test_cat_osdf_urls(httpserver: HTTPServer, get_client):
    """
    A bulk read of full osdf:// urls comes back keyed by namespace path.

    A catalogue records where each dataset lives, and records it as a url rather than a
    bare path -- the entries in examples/intake look like
    osdf:///chtc/PUBLIC/.../cesm2LE-FLNS.zarr. So a read driven by one arrives as a list
    of urls, and the caller has to be able to match the results back up to what it asked
    for.
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
