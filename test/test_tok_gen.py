"""
Copyright (C) 2024, Pelican Project, Morgridge Institute for Research

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
import threading
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.exceptions import ExpiredSignatureError
from scitokens import SciToken

from pelicanfs.exceptions import (
    InvalidDestinationURL,
    TokenIteratorException,
    TokenLocationNotSet,
)
from pelicanfs.token_generator import (
    TokenGenerator,
    TokenInfo,
    TokenOperation,
    is_valid_token,
    token_is_valid_and_acceptable,
)


@pytest.fixture
def dummy_dir_resp():
    class XPelTokGen:
        Issuers = ["https://trusted-issuer.example.com"]

    class XPelNs:
        Namespace = "/namespace/prefix"

    class DirResp:
        XPelTokGenHdr = XPelTokGen
        XPelNsHdr = XPelNs

    return DirResp()


@pytest.fixture
def token_generator_factory(dummy_dir_resp):
    def factory(url="https://example.com/namespace/prefix/file.txt", token_name="mytoken", operation=TokenOperation.TokenRead):
        return TokenGenerator(destination_url=url, token_name=token_name, dir_resp=dummy_dir_resp, operation=operation)

    return factory


def generate_ec_key_pair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def create_es256_scitoken_with_public_key(exp_offset_seconds=3600, aud=None, issuer=None, scopes=None):
    private_key_pem, public_key_pem = generate_ec_key_pair()

    token = SciToken(key=private_key_pem, algorithm="ES256")
    token["iss"] = issuer or "https://trusted-issuer.example.com"
    token["aud"] = aud or "/namespace/prefix"
    token["scope"] = " ".join(scopes) if scopes else ""

    jwt = token.serialize(issuer=token["iss"], lifetime=exp_offset_seconds)
    return jwt, public_key_pem


class TokenIterator:
    def __init__(self, tokens, raise_on_next=None):
        self.tokens = iter(tokens)
        self.raise_on_next = raise_on_next

    def __iter__(self):
        return self

    def __next__(self):
        if self.raise_on_next:
            raise self.raise_on_next
        return next(self.tokens)


def test_get_token_without_token_location(token_generator_factory):
    tg = token_generator_factory()
    tg.set_token_location(None)

    with pytest.raises(TokenLocationNotSet) as e:
        tg.get_token()
    assert "TokenLocation must be set" in str(e.value)


def test_get_token_with_empty_token_location(token_generator_factory):
    tg = token_generator_factory()
    tg.set_token_location("")

    with pytest.raises(TokenLocationNotSet) as e:
        tg.get_token()
    assert "TokenLocation must be set" in str(e.value)


def test_get_token_with_url_no_path(token_generator_factory):
    tg = token_generator_factory(url="https://example.com")
    tg.set_token_location("/valid/location")

    with pytest.raises(InvalidDestinationURL) as e:
        tg.get_token()
    assert "Invalid DestinationURL" in str(e.value)


def test_get_token_iterator_raises_unexpected(token_generator_factory):
    tg = token_generator_factory()
    tg.set_token_location("/valid/location")

    tg.Iterator = TokenIterator([], raise_on_next=RuntimeError("Iterator failure"))

    with pytest.raises(TokenIteratorException) as e:
        tg.get_token()
    assert "Failed to fetch tokens due to iterator error" in str(e.value)


def test_get_token_returns_first_valid_token(token_generator_factory, monkeypatch, dummy_dir_resp):
    tg = token_generator_factory()
    tg.TokenLocation = "/some/valid/location"
    tg.DestinationURL = "https://example.com/namespace/prefix/file.txt"
    tg.DirResp = dummy_dir_resp
    tg.Operation = TokenOperation.TokenRead

    token1, public_key1 = create_es256_scitoken_with_public_key(exp_offset_seconds=3600, scopes=["storage.read"])
    token2, public_key2 = create_es256_scitoken_with_public_key(exp_offset_seconds=7200, scopes=["storage.read"])

    call_state = {"first": True}

    original_deserialize = SciToken.deserialize

    def deserialize_with_insecure(*args, **kwargs):
        if call_state["first"]:
            kwargs["public_key"] = public_key1
            call_state["first"] = False
        else:
            kwargs["public_key"] = public_key2
        kwargs["insecure"] = True
        return original_deserialize(*args, **kwargs)

    monkeypatch.setattr("pelicanfs.token_generator.SciToken.deserialize", deserialize_with_insecure)

    tg.Iterator = TokenIterator([token1, token2])
    tg.token = None

    token = tg.get_token()

    assert token == token1  # First valid token should be returned


def test_get_token_thread_safety(token_generator_factory, monkeypatch):
    tg = token_generator_factory()
    tg.TokenLocation = "/some/valid/location"

    # Provide a single valid token
    valid_token, public_key = create_es256_scitoken_with_public_key(exp_offset_seconds=3600, scopes=["storage.read"])

    original_deserialize = SciToken.deserialize

    def deserialize_with_insecure(*args, **kwargs):
        kwargs["public_key"] = public_key
        kwargs["insecure"] = True  # force insecure=True
        return original_deserialize(*args, **kwargs)

    monkeypatch.setattr("pelicanfs.token_generator.SciToken.deserialize", deserialize_with_insecure)

    tg.Iterator = TokenIterator([valid_token])
    tg.token = None

    results = []
    exceptions = []

    def worker():
        try:
            tok = tg.get_token()
            results.append(tok)
        except Exception as e:
            exceptions.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(tok == valid_token for tok in results)
    assert not exceptions


def test_token_is_valid_and_acceptable_with_real_token(dummy_dir_resp, monkeypatch):
    web_token, public_key = create_es256_scitoken_with_public_key(exp_offset_seconds=3600, aud="/namespace/prefix", issuer="https://trusted-issuer.example.com", scopes=["storage.read"])
    original_deserialize = SciToken.deserialize

    def deserialize_with_insecure(*args, **kwargs):
        kwargs["public_key"] = public_key
        kwargs["insecure"] = True  # force insecure=True
        return original_deserialize(*args, **kwargs)

    monkeypatch.setattr("pelicanfs.token_generator.SciToken.deserialize", deserialize_with_insecure)

    valid, expiry = token_is_valid_and_acceptable(web_token, "/namespace/prefix/file.txt", dummy_dir_resp, TokenOperation.TokenRead)

    assert valid
    assert expiry > datetime.now(timezone.utc)


def test_token_is_valid_and_acceptable_expired_token(dummy_dir_resp, monkeypatch):
    web_token, public_key = create_es256_scitoken_with_public_key(exp_offset_seconds=-10, issuer="https://trusted-issuer.example.com")
    original_deserialize = SciToken.deserialize

    def deserialize_with_insecure(*args, **kwargs):
        kwargs["public_key"] = public_key
        kwargs["insecure"] = True  # force insecure=True
        return original_deserialize(*args, **kwargs)

    monkeypatch.setattr("pelicanfs.token_generator.SciToken.deserialize", deserialize_with_insecure)

    valid, expiry = token_is_valid_and_acceptable(web_token, "/namespace/prefix/file.txt", dummy_dir_resp, TokenOperation.TokenRead)
    assert not valid
    assert expiry <= datetime.now(timezone.utc)


def test_token_generator_get_token_returns_valid_token(token_generator_factory, dummy_dir_resp, monkeypatch):
    tg = token_generator_factory()
    tg.TokenLocation = "/some/valid/location"
    web_token, public_key = create_es256_scitoken_with_public_key(exp_offset_seconds=3600, aud="/namespace/prefix", issuer="https://trusted-issuer.example.com", scopes=["storage.read"])

    tg.Iterator = TokenIterator([web_token])
    tg.Operation = TokenOperation.TokenRead
    tg.DirResp = dummy_dir_resp

    original_deserialize = SciToken.deserialize

    def deserialize_with_insecure(*args, **kwargs):
        kwargs["public_key"] = public_key
        kwargs["insecure"] = True  # force insecure=True
        return original_deserialize(*args, **kwargs)

    monkeypatch.setattr("pelicanfs.token_generator.SciToken.deserialize", deserialize_with_insecure)

    tg.token = None  # Clear any existing token

    token = tg.get_token()
    assert token == web_token


def test_token_generator_get_token_fallback(token_generator_factory, dummy_dir_resp, monkeypatch):
    tg = token_generator_factory()

    tg.TokenLocation = "/some/valid/location"
    tg.DestinationURL = "https://example.com/namespace/prefix/file.txt"

    tg.DirResp = dummy_dir_resp
    tg.Operation = TokenOperation.TokenRead

    expired = datetime.now(timezone.utc) - timedelta(seconds=10)
    tg.token = TokenInfo("expired", expired)

    web_token, public_key = create_es256_scitoken_with_public_key(exp_offset_seconds=3600, scopes=["storage.read"], issuer="https://trusted-issuer.example.com")

    tokens = [
        "invalidtokenstring",  # Invalid token (will fail deserialize)
        web_token,
    ]

    tg.Iterator = TokenIterator(tokens)

    original_deserialize = SciToken.deserialize

    def deserialize_with_insecure(*args, **kwargs):
        kwargs["public_key"] = public_key
        kwargs["insecure"] = True  # force insecure=True
        return original_deserialize(*args, **kwargs)

    monkeypatch.setattr("pelicanfs.token_generator.SciToken.deserialize", deserialize_with_insecure)

    token = tg.get_token()
    assert token is not None
    assert token != "invalidtokenstring"


def test_token_generator_get_token_raises_when_no_valid_token(token_generator_factory):
    tg = token_generator_factory()
    tg.token = None

    tokens = ["badtoken1", "badtoken2"]

    tg.Iterator = TokenIterator(tokens)

    with pytest.raises(TokenLocationNotSet) as excinfo:
        tg.get_token()
    assert "TokenLocation must be set" in str(excinfo.value)


def test_token_generator_setters_and_copy(token_generator_factory):
    tg = token_generator_factory()
    tg.set_token_location("/my/location")
    tg.set_token_name("newname")
    assert tg.TokenLocation == "/my/location"
    assert tg.TokenName == "newname"

    copy_tg = tg.copy()
    assert copy_tg.DestinationURL == tg.DestinationURL
    assert copy_tg.TokenName == tg.TokenName
    assert copy_tg.DirResp == tg.DirResp
    assert copy_tg.Operation == tg.Operation
    assert copy_tg.token is None
    assert copy_tg.Iterator is None


def test_is_valid_token_real_token_warning(caplog):
    web_token, public_key = create_es256_scitoken_with_public_key(exp_offset_seconds=-1)

    try:
        # This will raise because the token is expired
        SciToken.deserialize(web_token, insecure=True, public_key=public_key)
        pytest.fail("Expected ExpiredSignatureError but token was accepted")
    except ExpiredSignatureError:
        # Re-decode manually without verifying 'exp' or 'aud'
        payload = jwt.decode(
            web_token,
            public_key,
            algorithms=["ES256"],
            options={"verify_exp": False, "verify_aud": False},
        )

        # Create a new SciToken and manually set claims
        token = SciToken()
        for key, value in payload.items():
            token[key] = value
        token._unverified_claims = payload
        token._issuer = payload.get("iss")

    result = is_valid_token(token, warn=True)
    assert not result
    assert "expired or about to expire" in caplog.text
