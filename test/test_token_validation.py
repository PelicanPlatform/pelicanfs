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
import logging
import time
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from scitokens import SciToken
from scitokens.utils.keycache import KeyCache

from pelicanfs.token_generator import (
    TokenGenerationOpts,
    is_valid_token,
    token_is_valid_and_acceptable,
)


@pytest.fixture(scope="module")
def ec_keys():
    """Generate reusable EC (P-256) keys."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8, encryption_algorithm=serialization.NoEncryption()).decode()

    public_pem = private_key.public_key().public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    # Also return private key object for easier token creation
    return {
        "private_pem": private_pem,
        "private_key_obj": private_key,
        "public_pem": public_pem.encode("utf-8"),
        "public_key_obj": private_key.public_key(),
    }


@pytest.fixture(scope="module")
def issuer():
    return "https://issuer.test"


@pytest.fixture(scope="module", autouse=True)
def setup_keycache(issuer, ec_keys):
    """
    Register EC public key in the SciTokens key cache.
    Uses the public key object.
    """
    KeyCache.getinstance().addkeyinfo(issuer=issuer, key_id="key1", public_key=ec_keys["public_key_obj"], cache_timer=3600)  # Must match the kid used in tokens


@pytest.fixture
def scope():
    return "storage.read"


def create_token(ec_keys, issuer, exp_offset_sec, scope_val) -> SciToken:
    """Create a signed SciToken using EC private key object."""

    # Use the private key object for signing
    token = SciToken(key=ec_keys["private_key_obj"], algorithm="ES256", key_id="key1")

    token["sub"] = "test-subject"
    token["scope"] = scope_val

    # We can either set exp and iss as claims, or rely on serialize to set them
    # Let's rely on serialize for iss and exp, so only set sub, aud, scope here

    # Serialize with issuer and lifetime to set exp and iat automatically
    token_bytes = token.serialize(issuer=issuer, lifetime=exp_offset_sec)

    # If you want to return the token object, deserialize it to reflect verified claims
    # decoded_token = SciToken.deserialize(token_bytes)

    # For your tests, you may want to return token_bytes (serialized token as bytes)
    # or return the deserialized token. Adjust as needed.
    # Here, return token_bytes for is_valid_token() compatibility

    return token_bytes


# ------------------------
# Tests: is_valid_token
# ------------------------


def test_valid_token(ec_keys, issuer, scope, caplog):
    token = create_token(ec_keys, issuer, 3600, scope)
    deserialized = SciToken.deserialize(token, insecure=True, public_key=ec_keys["public_pem"])
    with caplog.at_level(logging.WARNING):
        assert is_valid_token(deserialized, scope=scope, issuer=[issuer])
        # No warnings expected here
        assert caplog.records == []


def test_token_expiration_later(ec_keys, issuer, scope, caplog):
    token = create_token(ec_keys, issuer, 3600, scope)
    deserialized = SciToken.deserialize(token, insecure=True, public_key=ec_keys["public_pem"])

    # Simulate different expiration
    past_time = time.time() - 7200  # 2 hours ago
    deserialized.update_claims({"exp": past_time})

    with caplog.at_level(logging.WARNING):
        valid = is_valid_token(deserialized, scope=scope, issuer=[issuer])
        assert not valid
        assert any("Token expired or about to expire" in message for message in caplog.messages)


def test_issuer_mismatch(ec_keys, scope, caplog):
    token = create_token(ec_keys, "https://bad-issuer", 3600, scope)
    deserialized = SciToken.deserialize(token, insecure=True, public_key=ec_keys["public_pem"])

    with caplog.at_level(logging.WARNING):
        valid = is_valid_token(deserialized, scope=scope, issuer=["https://issuer.test"])
        assert not valid
        assert any("Token issuer" in message and "not in allowed list" in message for message in caplog.messages)


def test_scope_mismatch(ec_keys, issuer, caplog):
    token = create_token(ec_keys, issuer, 3600, "other.scope")
    deserialized = SciToken.deserialize(token, insecure=True, public_key=ec_keys["public_pem"])

    with caplog.at_level(logging.WARNING):
        valid = is_valid_token(deserialized, scope="storage.read", issuer=[issuer])
        assert not valid
        assert any("Token missing required scope" in message for message in caplog.messages)


def test_scope_unchecked(ec_keys, issuer, caplog):
    token = create_token(ec_keys, issuer, 3600, "admin.scope")
    deserialized = SciToken.deserialize(token, insecure=True, public_key=ec_keys["public_pem"])

    with caplog.at_level(logging.WARNING):
        valid = is_valid_token(deserialized, scope=None, issuer=[issuer])
        assert valid
        # No warnings expected here
        assert caplog.records == []


def test_multiple_audiences(ec_keys, issuer, scope, caplog):
    token = create_token(ec_keys, issuer, 3600, scope)
    deserialized = SciToken.deserialize(token, insecure=True, public_key=ec_keys["public_pem"])

    with caplog.at_level(logging.WARNING):
        valid = is_valid_token(deserialized, scope=scope, issuer=[issuer])
        assert valid
        # No warnings expected here
        assert caplog.records == []


# ------------------------
# Tests: token_is_valid_and_acceptable
# ------------------------


def mock_dir_resp(ns, issuers):
    class MockDirResp:
        class XPelNsHdr:
            Namespace = ns

        class XPelTokGenHdr:
            Issuers = issuers

    return MockDirResp()


def test_token_is_acceptable_read_scope(ec_keys, issuer):
    token = create_token(ec_keys, issuer, 3600, "storage.read")

    opts = TokenGenerationOpts(Operation="TokenRead")
    valid, expiry = token_is_valid_and_acceptable(token, object_name="namespace/file.txt", dir_resp=mock_dir_resp("namespace", [issuer]), opts=opts)
    assert valid
    assert expiry > datetime.now(timezone.utc)


def test_token_scope_mismatch_for_write(ec_keys, issuer):
    token = create_token(ec_keys, issuer, 3600, "storage.read")

    opts = TokenGenerationOpts(Operation="TokenWrite")
    valid, _ = token_is_valid_and_acceptable(token, object_name="namespace/data", dir_resp=mock_dir_resp("namespace", [issuer]), opts=opts)
    assert not valid


def test_token_valid_shared_write(ec_keys, issuer):
    token = create_token(ec_keys, issuer, 3600, "storage.modify storage.create")

    opts = TokenGenerationOpts(Operation="TokenSharedWrite")
    valid, _ = token_is_valid_and_acceptable(token, object_name="namespace/upload", dir_resp=mock_dir_resp("namespace", [issuer]), opts=opts)
    assert valid
