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

Tests that uproot reads NanoAOD-like files through pelican, as the Analysis Grand
Challenge does. uproot fetches many byte ranges at once with _cat_ranges.
"""

import itertools
import re
from collections.abc import Callable
from pathlib import Path

import awkward as ak
import numpy as np
import pytest
import uproot
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Request, Response

from pelicanfs.transfer_stats import ResponseFailure

BRANCHES = ["Jet_pt", "Jet_eta", "Jet_btagCSVV2", "Jet_jetId", "Muon_pt", "Muon_tightId"]
BASKETS = 5
EVENTS_PER_BASKET = 1000
Handler = Callable[[Request], Response]


def collection(rng: np.random.Generator, **fields) -> ak.Array:
    """Jagged records with 0 to 7 entries per event, with each field made by a function of a size."""
    counts = rng.integers(0, 8, EVENTS_PER_BASKET)
    return ak.zip({name: ak.unflatten(make(int(counts.sum())), counts) for name, make in fields.items()})


def events(rng: np.random.Generator) -> dict[str, ak.Array]:
    return {
        "Jet": collection(
            rng,
            pt=lambda n: rng.exponential(40, n),
            eta=lambda n: rng.normal(0, 2, n),
            btagCSVV2=rng.random,
            jetId=lambda n: rng.integers(0, 8, n),
        ),
        "Muon": collection(rng, pt=lambda n: rng.exponential(30, n), tightId=lambda n: rng.random(n) > 0.5),
    }


def write_file(path: Path, seed: int) -> None:
    rng = np.random.default_rng(seed)
    first = events(rng)
    with uproot.recreate(path, compression=uproot.LZMA(1)) as file:
        tree = file.mktree("Events", {name: array.type for name, array in first.items()})
        tree.extend(first)
        for _ in range(BASKETS - 1):
            tree.extend(events(rng))


@pytest.fixture(scope="module", name="root_files")
def fixture_root_files(tmp_path_factory) -> dict[str, Path]:
    """Small files with the layout of the AGC inputs, by their path in the federation."""
    directory = tmp_path_factory.mktemp("agc")
    files = {f"/agc/sample_{seed}.root": directory / f"sample_{seed}.root" for seed in range(2)}
    for seed, path in enumerate(files.values()):
        write_file(path, seed)
    return files


def serve_ranges(data: bytes) -> Handler:
    def handler(request: Request) -> Response:
        header = request.headers.get("Range")
        if request.method == "HEAD" or header is None:
            return Response(data)
        first, last = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", header).groups())
        last = min(last, len(data) - 1)
        return Response(data[first : last + 1], status=206, headers={"Content-Range": f"bytes {first}-{last}/{len(data)}"})

    return handler


def fail_after(good_gets: int, handler: Handler, failure: Handler) -> Handler:
    """Pass the HEAD probes and the first good_gets GETs to handler, and fail other GETs."""
    gets = itertools.count()

    def flaky(request: Request) -> Response:
        return failure(request) if request.method == "GET" and next(gets) >= good_gets else handler(request)

    return flaky


def service_unavailable(_request: Request) -> Response:
    return Response(status=503)


def truncated_body(_request: Request) -> Response:
    """A 206 whose body is shorter than its Content-Length."""
    response = Response(b"short", status=206)
    response.headers["Content-Length"] = "100"
    return response


def serve(cache: HTTPServer, root_files: dict[str, Path], wrap: Callable[[Handler], Handler] = lambda handler: handler) -> None:
    for path, file in root_files.items():
        cache.expect_request(path).respond_with_handler(wrap(serve_ranges(file.read_bytes())))


def get_ranges(cache: HTTPServer) -> list[tuple[str | None, Response]]:
    return [(request.headers.get("Range"), response) for request, response in cache.log if request.method == "GET"]


@pytest.fixture(name="caches")
def fixture_caches(threaded_httpserver: HTTPServer, threaded_httpserver2: HTTPServer) -> tuple[HTTPServer, HTTPServer]:
    threaded_httpserver.clear()
    threaded_httpserver2.clear()
    return threaded_httpserver, threaded_httpserver2


@pytest.fixture(name="urls")
def fixture_urls(httpserver: HTTPServer, caches, root_files) -> list[str]:
    """The pelican:// URL of each file, with a director that lists both caches."""
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    for path in root_files:
        links = ", ".join(f'<{cache.url_for(path)}>; rel="duplicate"; pri={pri}; depth=1' for pri, cache in enumerate(caches, 1))
        httpserver.expect_request(path).respond_with_data(
            "",
            status=307,
            headers={"Link": links, "Location": caches[0].url_for(path), "X-Pelican-Namespace": "namespace=/agc"},
        )
    return [f"pelican://{httpserver.host}:{httpserver.port}{path}" for path in root_files]


@pytest.fixture(name="options")
def fixture_options(get_client) -> dict:
    """uproot passes these to fsspec, which makes the filesystem."""
    return {"get_client": get_client, "skip_instance_cache": True}


@pytest.fixture(scope="module", name="local_events")
def fixture_local_events(root_files) -> ak.Array:
    return uproot.concatenate({str(file): "Events" for file in root_files.values()}, BRANCHES)


@pytest.mark.parametrize("step_size", [1500, "50 kB"])
def test_iterate_matches_local_read(urls, caches, root_files, options, local_events, step_size):
    serve(caches[0], root_files)

    batches = list(uproot.iterate(dict.fromkeys(urls, "Events"), BRANCHES, step_size=step_size, **options))

    assert len(batches) > 1
    assert ak.to_list(ak.concatenate(batches)) == ak.to_list(local_events)


def test_reads_only_what_it_needs(urls, caches, root_files, options):
    serve(caches[0], root_files)

    with uproot.open({urls[0]: "Events"}, **options) as tree:
        tree["Jet_pt"].array()

    reads = get_ranges(caches[0])
    assert all(header is not None for header, _ in reads)
    assert sum(len(response.get_data()) for _, response in reads) < next(iter(root_files.values())).stat().st_size / 2


@pytest.mark.parametrize(("failure", "status"), [(service_unavailable, 503), (truncated_body, ResponseFailure.BODY)])
def test_cache_failure_during_read_moves_to_next_cache(urls, caches, root_files, options, local_events, failure, status):
    first, second = caches
    serve(first, root_files, lambda handler: fail_after(3, handler, failure))
    serve(second, root_files)

    with uproot.open({urls[0]: "Events"}, **options) as tree:
        arrays = tree.arrays(BRANCHES)
        stats = tree.file.source._fs.get_transfer_stats()

    assert ak.to_list(arrays) == ak.to_list(local_events[: tree.num_entries])
    assert len(get_ranges(first)) > 3
    assert get_ranges(second)
    assert stats.responses[(f"https://{first.host}:{first.port}", "GET", status)] > 0
    assert stats.failed_operations == 0


def test_all_caches_failing_raises(urls, caches, root_files, options):
    first, second = caches
    serve(first, root_files, lambda handler: fail_after(3, handler, service_unavailable))
    serve(second, root_files, lambda handler: fail_after(0, handler, service_unavailable))

    # uproot 5.7.7 gives a TypeError on the error from _cat_ranges, and later versions an OSError
    with uproot.open({urls[0]: "Events"}, **options) as tree, pytest.raises((TypeError, OSError), match="ClientResponseError"):
        tree.arrays(BRANCHES)
