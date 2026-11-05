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

import logging
from unittest.mock import patch

import aiohttp
import pytest
from fsspec.asyn import sync
from pytest_httpserver import HTTPServer

from pelicanfs.core import PelicanFileSystem
from pelicanfs.exceptions import (
    BadDirectorResponse,
    DirectorError,
    InvalidMetadata,
    NoAvailableSource,
)
from pelicanfs.token_content_iterator import TokenContentIterator, TokenDiscoveryMethod

# Nothing listens on port 1, so a connection fails at once.
DEAD_CACHE = "https://127.0.0.1:1/foo/bar"


def serve_discovery(server: HTTPServer) -> None:
    server.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": server.url_for("/")})


def serve_director(server: HTTPServer, *caches: str) -> None:
    serve_discovery(server)
    links = ", ".join(f'<{cache}>; rel="duplicate"; pri={pri}; depth=1' for pri, cache in enumerate(caches, 1))
    server.expect_oneshot_request("/foo/bar", method="GET").respond_with_data(
        "",
        status=307,
        headers={"Link": links, "Location": caches[0], "X-Pelican-Namespace": "namespace=/foo"},
    )


@pytest.fixture(name="pelfs")
def fixture_pelfs(httpserver: HTTPServer, get_client):
    return PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True)


def test_authz_query_is_not_logged(httpserver: HTTPServer, pelfs, caplog):
    serve_director(httpserver, httpserver.url_for("/foo/bar"))
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=200)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("ok")

    with caplog.at_level(logging.DEBUG, logger="fsspec"):
        assert pelfs.cat("/foo/bar?authz=SECRET") == b"ok"

    # The test server logs its own requests through werkzeug
    messages = [record.getMessage() for record in caplog.records if record.name.startswith("fsspec")]
    assert any("authz=<redacted>" in message for message in messages)
    assert not any("SECRET" in message for message in messages)


def test_authz_query_is_not_in_access_data(httpserver: HTTPServer, pelfs):
    serve_director(httpserver, httpserver.url_for("/foo/bar"))
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=200)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=500)

    with pytest.raises(aiohttp.ClientResponseError):
        pelfs.cat("/foo/bar?authz=SECRET")

    responses, found = pelfs.get_access_data().get_responses("/foo/bar")
    assert found
    assert "500" in responses[0].error
    assert "SECRET" not in repr(responses)


def test_all_probes_failing_reports_each_cache(httpserver: HTTPServer, pelfs, caplog):
    cache = httpserver.url_for("/foo/bar")
    serve_director(httpserver, cache, DEAD_CACHE)
    httpserver.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data("", status=503)

    with caplog.at_level(logging.WARNING, logger="fsspec.pelican"), pytest.raises(NoAvailableSource) as excinfo:
        sync(pelfs.loop, pelfs.get_working_cache, "/foo/bar?authz=SECRET")

    message = str(excinfo.value)
    assert "/foo/bar" in message
    assert f"{cache}?authz=<redacted>: HTTP 503" in message
    assert "127.0.0.1:1" in message
    assert "SECRET" not in message
    assert "HTTP 503" in caplog.text


def test_probe_failure_is_logged_when_a_later_cache_works(httpserver: HTTPServer, httpserver2: HTTPServer, pelfs, caplog):
    httpserver2.clear()
    good_cache = httpserver2.url_for("/foo/bar")
    serve_director(httpserver, httpserver.url_for("/foo/bar"), good_cache)
    httpserver.expect_oneshot_request("/foo/bar", method="HEAD").respond_with_data("", status=503)
    httpserver2.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=200)

    with caplog.at_level(logging.WARNING, logger="fsspec.pelican"):
        assert sync(pelfs.loop, pelfs.get_working_cache, "/foo/bar")[0] == good_cache

    assert f"{httpserver.url_for('/foo/bar')}: HTTP 503" in caplog.text


def test_director_list_without_caches_is_reported(httpserver: HTTPServer, pelfs):
    serve_discovery(httpserver)
    httpserver.expect_request("/foo/bar", method="GET").respond_with_data("", status=307, headers={"X-Pelican-Namespace": "namespace=/foo"})

    with pytest.raises(NoAvailableSource, match="no caches"):
        pelfs.cat("/foo/bar")


@pytest.mark.parametrize("direct_reads", [False, True])
def test_director_error_status_is_reported(httpserver: HTTPServer, get_client, direct_reads):
    serve_discovery(httpserver)
    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, direct_reads=direct_reads)
    path = "/api/v1.0/director/origin/foo/bar" if direct_reads else "/foo/bar"
    httpserver.expect_request(path, method="GET").respond_with_data("", status=503)

    with pytest.raises(DirectorError, match="503") as excinfo:
        pelfs.cat("/foo/bar?authz=SECRET")

    assert isinstance(excinfo.value, NoAvailableSource)
    assert "SECRET" not in str(excinfo.value)


def test_director_error_does_not_block_preferred_caches(httpserver: HTTPServer, get_client):
    serve_discovery(httpserver)
    cache = httpserver.url_for("/foo/bar")
    httpserver.expect_oneshot_request("/foo/bar", method="GET").respond_with_data("", status=404)
    httpserver.expect_request("/foo/bar", method="HEAD").respond_with_data("", status=200)
    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, preferred_caches=[httpserver.url_for("/")])

    assert sync(pelfs.loop, pelfs.get_working_cache, "/foo/bar")[0] == cache


def test_origin_without_location_is_reported(httpserver: HTTPServer, get_client):
    serve_discovery(httpserver)
    httpserver.expect_request("/api/v1.0/director/origin/foo/bar", method="GET").respond_with_data("", status=307)
    pelfs = PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, direct_reads=True)

    with pytest.raises(NoAvailableSource, match="no origin"):
        pelfs.cat("/foo/bar")


def test_discovery_failure_reports_status(httpserver: HTTPServer, pelfs):
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_data("", status=503)

    with pytest.raises(InvalidMetadata, match="503"):
        pelfs.cat("/foo/bar")


def test_metadata_without_director_is_reported(httpserver: HTTPServer, pelfs):
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({})

    with pytest.raises(InvalidMetadata, match="director_endpoint"):
        pelfs.cat("/foo/bar")


def test_propfind_without_link_reports_status(httpserver: HTTPServer, pelfs):
    serve_discovery(httpserver)
    httpserver.expect_request("/foo/bar", method="PROPFIND").respond_with_data("", status=503)

    with pytest.raises(BadDirectorResponse, match="503"):
        pelfs.ls("/foo/bar")


def test_default_bearer_token_is_not_logged(tmp_path, caplog):
    token_file = tmp_path / "token"
    token_file.write_text("opaque-secret-token")
    iterator = TokenContentIterator(location=None, name="test")
    iterator.method_index = iterator.get_method_index(TokenDiscoveryMethod.DEFAULT_BEARER_TOKEN)

    with (
        patch("pelicanfs.token_content_iterator.default_bearer_token_file", return_value=str(token_file)),
        caplog.at_level(logging.DEBUG, logger="fsspec.pelican"),
    ):
        assert next(iterator) == "opaque-secret-token"

    assert "opaque" not in caplog.text
