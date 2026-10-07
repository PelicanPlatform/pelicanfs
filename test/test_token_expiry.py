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

import itertools
from datetime import datetime, timedelta, timezone

import pytest
from fsspec.asyn import sync

from pelicanfs.core import TOKEN_REFRESH_MARGIN, PelicanFileSystem
from pelicanfs.dir_header_parser import DirectorResponse, XPelNs, XPelTokGen
from pelicanfs.token_generator import TokenGenerator, TokenInfo, TokenOperation

DATA_URL = "https://cache.example.com/namespace/prefix/file.txt"


@pytest.fixture
def token_required_dir_resp():
    x_pel_tok_gen = XPelTokGen(issuers=["https://trusted-issuer.example.com"])
    x_pel_ns = XPelNs(namespace="/namespace/prefix", require_token=True)
    return DirectorResponse(object_servers=["https://cache.example.com"], location=None, x_pel_tok_gen_hdr=x_pel_tok_gen, x_pel_ns_hdr=x_pel_ns)


@pytest.fixture
def pelfs():
    """
    A filesystem with the director already known, so _handle_token_generation never has
    to discover federation metadata and the tests need no server.
    """
    fs = PelicanFileSystem(skip_instance_cache=True)
    fs.director_url = "https://director.example.com/"
    return fs


def install_generator_stub(monkeypatch, expiry):
    """
    Replace TokenGenerator.get_token with one that hands out "token-<n>" on its n-th call,
    recording the given expiry the way the real get_token does. The number in the token
    name is how a test tells whether the generator ran again.
    """
    numbers = itertools.count(1)

    def get_token(self):
        contents = f"token-{next(numbers)}"
        self.token = TokenInfo(contents, expiry)
        return contents

    monkeypatch.setattr(TokenGenerator, "get_token", get_token)


def handle(pelfs, dir_resp):
    # Driven through fsspec's loop, the way every entry point into the filesystem reaches it.
    return sync(pelfs.loop, pelfs._handle_token_generation, DATA_URL, dir_resp, TokenOperation.TokenRead)


def authorization_header(pelfs):
    http_fs = pelfs.http_file_system
    if getattr(http_fs, "session", None):
        return http_fs.session.headers.get("Authorization")
    return http_fs._default_headers.get("Authorization")


def test_expired_generated_token_is_regenerated(pelfs, token_required_dir_resp, monkeypatch):
    """
    Once a generated token has passed its expiry, the next call must generate a new one
    rather than hand back the remembered token, and the new token must replace the old
    one both on the instance and in the HTTP filesystem's headers.
    """
    install_generator_stub(monkeypatch, datetime.now(timezone.utc) - timedelta(seconds=1))

    assert handle(pelfs, token_required_dir_resp) == "token-1"
    assert pelfs.token == "Bearer token-1"

    assert handle(pelfs, token_required_dir_resp) == "token-2", "the expired token was reused instead of being regenerated"
    assert pelfs.token == "Bearer token-2"
    assert authorization_header(pelfs) == "Bearer token-2", "the HTTP filesystem is still sending the expired token"


def test_token_about_to_expire_is_regenerated(pelfs, token_required_dir_resp, monkeypatch):
    """
    A token still inside the refresh margin would be expired by the time a request
    carrying it reached the server, so it is retired early.
    """
    install_generator_stub(monkeypatch, datetime.now(timezone.utc) + TOKEN_REFRESH_MARGIN / 2)

    assert handle(pelfs, token_required_dir_resp) == "token-1"
    assert handle(pelfs, token_required_dir_resp) == "token-2", "a token about to expire was reused instead of being regenerated"


def test_unexpired_generated_token_is_reused(pelfs, token_required_dir_resp, monkeypatch):
    install_generator_stub(monkeypatch, datetime.now(timezone.utc) + timedelta(hours=1))

    assert handle(pelfs, token_required_dir_resp) == "token-1"
    assert handle(pelfs, token_required_dir_resp) == "token-1", "a token that has not expired was generated again"
    assert pelfs.token == "Bearer token-1"


def test_user_supplied_token_is_left_alone(token_required_dir_resp, monkeypatch):
    """
    A token the caller passed in headers is the caller's responsibility: it has no
    recorded expiry, is never re-checked and is never replaced by a generated one.
    """

    def get_token(self):
        pytest.fail("the caller's token was replaced by a generated one")

    monkeypatch.setattr(TokenGenerator, "get_token", get_token)

    pelfs = PelicanFileSystem(skip_instance_cache=True, headers={"Authorization": "Bearer user-token"})
    pelfs.director_url = "https://director.example.com/"

    assert handle(pelfs, token_required_dir_resp) == "user-token"
    assert handle(pelfs, token_required_dir_resp) == "user-token"
    assert pelfs.token == "Bearer user-token"
    assert pelfs._token_expiry is None
