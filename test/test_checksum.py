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

import base64
import hashlib
import zlib

import pytest
from pytest_httpserver import HTTPServer

import pelicanfs.core
from pelicanfs.checksum import file_digests, parse_digest
from pelicanfs.exceptions import ChecksumMismatch, ChecksumMissing

OBJECT = "/ns/obj.txt"
CONTENTS = b"hello, world!"
CRC32C = "ce8f3c63"  # crc32c of CONTENTS
MD5 = base64.b64encode(hashlib.md5(CONTENTS).digest()).decode()


def test_crc32c_value():
    """Guards the constant the tests below rely on, with the standard check value."""
    import google_crc32c

    assert google_crc32c.value(b"123456789") == 0xE3069283
    assert google_crc32c.value(CONTENTS).to_bytes(4, "big").hex() == CRC32C


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ([f"crc32c={CRC32C}"], {"crc32c": bytes.fromhex(CRC32C)}),
        ([f"md5={MD5}, crc32c={CRC32C}"], {"md5": hashlib.md5(CONTENTS).digest(), "crc32c": bytes.fromhex(CRC32C)}),
        ([f"MD5={MD5}", f"crc32={zlib.crc32(CONTENTS):x}"], {"md5": hashlib.md5(CONTENTS).digest(), "crc32": zlib.crc32(CONTENTS).to_bytes(4, "big")}),
        # Leading zeros may be dropped from the hex form
        (["crc32=ff"], {"crc32": bytes.fromhex("000000ff")}),
        # XRootD sends crc32c in base64: https://github.com/xrootd/xrootd/issues/2456
        ([f"crc32c={base64.b64encode(bytes.fromhex(CRC32C)).decode()}"], {"crc32c": bytes.fromhex(CRC32C)}),
        # An MD5 sent under the crc32c name
        (["crc32c=0123456789abcdef0123456789abcdef"], {}),
        (["sha-256=abc, adler32=0001", "garbage", "crc32c=xyz"], {}),
    ],
)
def test_parse_digest(headers, expected):
    assert parse_digest(headers) == expected


def test_file_digests(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(CONTENTS)
    assert file_digests(path) == {
        "md5": hashlib.md5(CONTENTS).digest(),
        "crc32c": bytes.fromhex(CRC32C),
        "crc32": zlib.crc32(CONTENTS).to_bytes(4, "big"),
        "sha": hashlib.sha1(CONTENTS).digest(),
    }


def _serve(httpserver: HTTPServer, digest: str | None, contents: bytes = CONTENTS) -> dict[str, str]:
    """Serve OBJECT with one server as director and cache, answering a HEAD with `digest`."""
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": httpserver.url_for("/")})
    director_headers = {
        "Link": f'<{httpserver.url_for(OBJECT)}>; rel="duplicate"; pri=1; depth=1',
        "X-Pelican-Namespace": f"namespace=/ns, collections-url={httpserver.url_for('/')}",
    }
    httpserver.expect_request(OBJECT, method="GET").respond_with_data(contents, headers=director_headers)
    head_headers = {**director_headers, "Content-Length": str(len(contents))}
    if digest is not None:
        head_headers["Digest"] = digest
    httpserver.expect_request(OBJECT, method="HEAD").respond_with_data("", headers=head_headers)
    return director_headers


def _fs(httpserver: HTTPServer, get_client, get_webdav_client, **kwargs) -> pelicanfs.core.PelicanFileSystem:
    return pelicanfs.core.PelicanFileSystem(httpserver.url_for("/"), get_client=get_client, skip_instance_cache=True, get_webdav_client=get_webdav_client, **kwargs)


def _digest_requests(httpserver: HTTPServer) -> list[str]:
    return [request.headers["Want-Digest"] for request, _ in httpserver.log if "Want-Digest" in request.headers]


def test_get_verifies(httpserver: HTTPServer, get_client, get_webdav_client, tmp_path):
    _serve(httpserver, f"crc32c={CRC32C}")
    dest = tmp_path / "obj.txt"

    _fs(httpserver, get_client, get_webdav_client).get(OBJECT, str(dest))

    assert dest.read_bytes() == CONTENTS
    assert _digest_requests(httpserver) == ["md5,crc32c,crc32,sha"]


def test_get_mismatch_removes_file(httpserver: HTTPServer, get_client, get_webdav_client, tmp_path):
    _serve(httpserver, f"crc32c={CRC32C}", contents=b"corrupted!!!!")
    dest = tmp_path / "obj.txt"

    with pytest.raises(ChecksumMismatch, match="crc32c") as excinfo:
        _fs(httpserver, get_client, get_webdav_client).get(OBJECT, str(dest))

    assert excinfo.value.remote == CRC32C
    assert not dest.exists()


def test_get_without_digest(httpserver: HTTPServer, get_client, get_webdav_client, tmp_path):
    """A server that sends no digest is accepted unless checksums are required."""
    _serve(httpserver, None)
    dest = tmp_path / "obj.txt"

    _fs(httpserver, get_client, get_webdav_client).get(OBJECT, str(dest))
    assert dest.read_bytes() == CONTENTS

    with pytest.raises(ChecksumMissing):
        _fs(httpserver, get_client, get_webdav_client, require_checksums=True).get(OBJECT, str(dest))


def test_get_without_verification(httpserver: HTTPServer, get_client, get_webdav_client, tmp_path):
    _serve(httpserver, f"crc32c={CRC32C}", contents=b"corrupted!!!!")
    dest = tmp_path / "obj.txt"

    _fs(httpserver, get_client, get_webdav_client, verify_checksums=False).get(OBJECT, str(dest))

    assert dest.read_bytes() == b"corrupted!!!!"
    assert _digest_requests(httpserver) == []


def test_checksums(httpserver: HTTPServer, get_client, get_webdav_client):
    _serve(httpserver, f"md5={MD5},crc32c={CRC32C}")

    assert _fs(httpserver, get_client, get_webdav_client).checksums(OBJECT) == {"md5": hashlib.md5(CONTENTS).hexdigest(), "crc32c": CRC32C}


def _serve_upload(httpserver: HTTPServer, digest: str) -> None:
    base_url = httpserver.url_for("/")
    origin_url = httpserver.url_for(OBJECT)
    httpserver.expect_request("/.well-known/pelican-configuration").respond_with_json({"director_endpoint": base_url})
    httpserver.expect_request(f"/api/v1.0/director/origin{OBJECT}").respond_with_data("", status=307, headers={"Location": origin_url})
    httpserver.expect_request(OBJECT, method="PUT").respond_with_data(status=201)
    httpserver.expect_request(OBJECT, method="HEAD").respond_with_data("", headers={"Digest": digest})


@pytest.mark.parametrize("contents", [CONTENTS, b"other bytes"])
def test_put_verifies(httpserver: HTTPServer, get_client, get_webdav_client, tmp_path, contents):
    _serve_upload(httpserver, f"crc32c={CRC32C}")
    src = tmp_path / "obj.txt"
    src.write_bytes(contents)
    pelfs = _fs(httpserver, get_client, get_webdav_client)

    if contents == CONTENTS:
        pelfs.put_file(str(src), OBJECT)
    else:
        with pytest.raises(ChecksumMismatch):
            pelfs.put_file(str(src), OBJECT)
    assert _digest_requests(httpserver) == ["md5,crc32c,crc32,sha"]
