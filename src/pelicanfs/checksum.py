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

RFC 3230 instance digests, as the Pelican Go client uses them.
"""

import base64
import binascii
import hashlib
import logging
import os
import zlib
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

import google_crc32c

from .exceptions import ChecksumMismatch, ChecksumMissing

logger = logging.getLogger("fsspec.pelican")

# HTTP digest names, in the Go client's order. "sha" is SHA-1.
ALGORITHMS = ("md5", "crc32c", "crc32", "sha")
WANT_DIGEST = ",".join(ALGORITHMS)

_CHUNK_SIZE = 4 * 1024 * 1024


class _Hash(Protocol):
    def update(self, data: bytes, /) -> object: ...
    def digest(self) -> bytes: ...


class _CRC32:
    def __init__(self) -> None:
        self._value = 0

    def update(self, data: bytes, /) -> None:
        self._value = zlib.crc32(data, self._value)

    def digest(self) -> bytes:
        return self._value.to_bytes(4, "big")


def _new_hash(algorithm: str) -> _Hash:
    match algorithm:
        case "md5":
            return hashlib.md5(usedforsecurity=False)
        case "sha":
            return hashlib.sha1(usedforsecurity=False)
        case "crc32":
            return _CRC32()
        case "crc32c":
            return google_crc32c.Checksum()
    msg = f"Unknown checksum algorithm: {algorithm}"
    raise ValueError(msg)


def file_digests(path: str | os.PathLike[str], algorithms: Iterable[str] = ALGORITHMS) -> dict[str, bytes]:
    """Compute the digests of a local file."""
    hashes = {name: _new_hash(name) for name in algorithms}
    with Path(path).open("rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            for h in hashes.values():
                h.update(chunk)
    return {name: h.digest() for name, h in hashes.items()}


def _decode(algorithm: str, value: str) -> bytes | None:
    if algorithm in {"crc32", "crc32c"}:
        # XRootD sends crc32c base64-encoded, not hex: https://github.com/xrootd/xrootd/issues/2456
        if algorithm == "crc32c" and len(value) == 8 and value.endswith("=="):
            return base64.b64decode(value)
        # More than 32 bits: the XRootD multiuser plugin can send another algorithm under this name
        if len(value) > 8:
            logger.warning("Server sent %s=%s, which is too long for a 32-bit checksum; ignoring it", algorithm, value)
            return None
        return int(value, 16).to_bytes(4, "big")
    return base64.b64decode(value, validate=True)


def parse_digest(values: Iterable[str]) -> dict[str, bytes]:
    """
    Parse the values of the Digest headers of a response, ignoring algorithms not in ALGORITHMS.
    """
    result: dict[str, bytes] = {}
    for header in values:
        for entry in header.split(","):
            name, sep, value = entry.strip().partition("=")
            name = name.strip().lower()
            if not sep or name not in ALGORITHMS:
                if entry.strip():
                    logger.debug("Ignoring digest %r", entry.strip())
                continue
            try:
                decoded = _decode(name, value.strip())
            except (ValueError, binascii.Error):
                logger.warning("Cannot parse %s checksum value %r", name, value)
                continue
            if decoded is not None:
                result[name] = decoded
    return result


def to_hex(digests: dict[str, bytes]) -> dict[str, str]:
    return {name: value.hex() for name, value in digests.items()}


def verify(url: str, local: dict[str, bytes], remote: dict[str, bytes], *, require: bool) -> str | None:
    """
    Compare the digests of a local copy with the digests the server sent for `url`.

    Every algorithm in both is compared. Returns the algorithms that matched, comma
    separated, or None if none were compared.

    Raises ChecksumMismatch if a digest differs, and ChecksumMissing if `require` is set
    and no digest could be compared.
    """
    common = [name for name in ALGORITHMS if name in local and name in remote]
    for name in common:
        if local[name] != remote[name]:
            raise ChecksumMismatch(url, name, local[name].hex(), remote[name].hex())
    if not common:
        if require:
            msg = f"Checksum verification is required, but the server sent no usable checksum for {url}"
            raise ChecksumMissing(msg)
        logger.debug("No checksum from the server to verify %s", url)
        return None
    logger.debug("Checksums %s match for %s", ", ".join(common), url)
    return ",".join(common)
