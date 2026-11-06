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

import asyncio

import aiohttp
import pytest
from fsspec.callbacks import Callback
from pytest_httpserver import HTTPServer

from pelicanfs import PelicanFileSystem, TransferStats
from pelicanfs.transfer_stats import _TransferRecorder


def setup_director(httpserver: HTTPServer, *caches: HTTPServer) -> None:
    """Point the director at each cache, in order."""
    urls = [server.url_for("/foo/bar") for server in caches]
    link = ", ".join(f'<{url}>; rel="duplicate"; pri={i}; depth=1' for i, url in enumerate(urls, 1))
    headers = {"Link": link, "Location": urls[0], "X-Pelican-Namespace": "namespace=/foo"}
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("", status=307, headers=headers)


@pytest.fixture(name="pelfs")
def fixture_pelfs(httpserver: HTTPServer, get_client, get_webdav_client):
    return PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, get_webdav_client=get_webdav_client, skip_instance_cache=True)


def test_stats_start_empty():
    stats = TransferStats()

    assert stats.request_success_rate is None
    assert stats.operation_success_rate is None
    assert stats.average_rate is None


def test_rates():
    stats = TransferStats(requests=4, failed_requests=1, operations=2, failed_operations=1, bytes_read=30, bytes_written=10, busy_seconds=2.0)

    assert stats.request_success_rate == 0.75
    assert stats.operation_success_rate == 0.5
    assert stats.average_rate == 20.0


def test_concurrent_operations_share_busy_time(monkeypatch):
    now = 0.0
    monkeypatch.setattr("pelicanfs.transfer_stats.time.perf_counter", lambda: now)
    recorder = _TransferRecorder()

    with recorder.operation():
        now = 1.0
        with recorder.operation():
            now = 3.0
        assert recorder.snapshot().busy_seconds == 3.0
        now = 4.0
    now = 10.0
    with recorder.operation():
        now = 11.0

    assert recorder.snapshot().busy_seconds == 5.0
    assert recorder.snapshot().operations == 3


def test_cat(httpserver: HTTPServer, pelfs):
    setup_director(httpserver, httpserver)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("hello")

    assert pelfs.cat("/foo/bar") == b"hello"

    stats = pelfs.get_transfer_stats()
    assert stats.requests == len(httpserver.log)
    assert stats.failed_requests == 0
    assert stats.operations == 1
    assert stats.bytes_read == 5
    assert stats.busy_seconds > 0
    assert stats.average_rate > 0


def test_retry_is_a_failed_request_but_not_a_failed_operation(httpserver: HTTPServer, httpserver2: HTTPServer, pelfs):
    setup_director(httpserver, httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=500)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_data("hello")

    assert pelfs.cat("/foo/bar") == b"hello"

    stats = pelfs.get_transfer_stats()
    assert stats.failed_requests == 1
    assert stats.operations == 1
    assert stats.failed_operations == 0
    assert stats.operation_success_rate == 1.0


def test_exhausted_retries_fail_the_operation(httpserver: HTTPServer, httpserver2: HTTPServer, pelfs):
    setup_director(httpserver, httpserver, httpserver2)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=500)
    httpserver2.expect_request("/foo/bar", method="GET").respond_with_data("", status=500)

    with pytest.raises(aiohttp.ClientResponseError):
        pelfs.cat("/foo/bar")

    stats = pelfs.get_transfer_stats()
    assert stats.failed_requests == 2
    assert stats.failed_operations == 1
    assert stats.operation_success_rate == 0.0


def test_connection_failure_is_a_failed_request(httpserver: HTTPServer, pelfs):
    url = "https://127.0.0.1:1/foo/bar"
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": f'<{url}>; rel="duplicate"; pri=1; depth=1', "Location": url, "X-Pelican-Namespace": "namespace=/foo"},
    )

    with pytest.raises(Exception, match="No working cache"):
        pelfs.cat("/foo/bar")

    stats = pelfs.get_transfer_stats()
    assert stats.failed_requests >= 1
    assert stats.failed_operations == 1


def test_missing_object_is_not_a_failure(httpserver: HTTPServer, pelfs):
    setup_director(httpserver, httpserver)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=404)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=404)

    with pytest.raises(FileNotFoundError):
        pelfs.cat("/foo/bar")

    stats = pelfs.get_transfer_stats()
    assert stats.operations == 1
    assert stats.failed_requests == 0
    assert stats.failed_operations == 0


def test_get_counts_streamed_bytes_and_keeps_the_callback(httpserver: HTTPServer, pelfs, tmp_path):
    setup_director(httpserver, httpserver)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("hello")
    callback = Callback()

    pelfs.get_file("/foo/bar", tmp_path / "bar", callback=callback)

    stats = pelfs.get_transfer_stats()
    assert (tmp_path / "bar").read_bytes() == b"hello"
    assert stats.bytes_read == 5
    assert stats.operations == 1
    assert (callback.size, callback.value) == (5, 5)


def test_put_counts_bytes_written(httpserver: HTTPServer, pelfs, tmp_path):
    source = tmp_path / "bar"
    source.write_bytes(b"hello")
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    httpserver.expect_request("/api/v1.0/director/origin/foo/bar").respond_with_data("", status=200, headers={"Location": httpserver.url_for("/foo/bar")})
    httpserver.expect_request("/foo/bar", method="PUT").respond_with_data("", status=201)

    pelfs.put_file(str(source), "/foo/bar")

    stats = pelfs.get_transfer_stats()
    assert stats.bytes_written == 5
    assert stats.bytes_read == 0
    assert stats.operations == 1
    assert stats.failed_operations == 0


def test_read_of_open_file(httpserver: HTTPServer, pelfs):
    setup_director(httpserver, httpserver)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("hello")

    with pelfs.open("/foo/bar", block_size=0) as f:
        assert f.read() == b"hello"

    stats = pelfs.get_transfer_stats()
    assert stats.bytes_read == 5
    assert stats.operations == 1


def test_read_of_async_open_file(httpserver: HTTPServer, get_client, get_webdav_client):
    setup_director(httpserver, httpserver)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("hello")

    async def read() -> PelicanFileSystem:
        pelfs = PelicanFileSystem(httpserver.url_for("/"), asynchronous=True, get_client=get_client, get_webdav_client=get_webdav_client, skip_instance_cache=True)
        fp = await pelfs.open_async("/foo/bar")
        assert await fp.read() == b"hello"
        await fp.close()
        await (await pelfs.http_file_system.set_session()).close()
        return pelfs

    stats = asyncio.run(read()).get_transfer_stats()
    assert stats.bytes_read == 5
    assert stats.operations == 1


def test_webdav_requests_are_counted(httpserver: HTTPServer, pelfs):
    setup_director(httpserver, httpserver)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data("", status=207)

    pelfs.exists("/foo/bar")

    assert sum(req.method == "PROPFIND" for req, _ in httpserver.log) >= 1
    assert pelfs.get_transfer_stats().requests == len(httpserver.log)


def test_stats_are_per_object(httpserver: HTTPServer, pelfs, get_client):
    setup_director(httpserver, httpserver)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("hello")
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("hello")
    other = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)

    pelfs.cat("/foo/bar")

    assert other.get_transfer_stats() == TransferStats()
