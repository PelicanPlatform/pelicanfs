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
import time
from pathlib import Path

import pytest

from pelicanfs import token_generator
from pelicanfs.log_utils import TRACE, TokenForLog, trace
from pelicanfs.token_generator import TokenOperation, token_is_valid_and_acceptable


@pytest.mark.parametrize(
    "secret",
    [
        "opaque-secret-abc",
        "key.secret",
        "first.second.third",
        '{"client_secret": "s3cret"}',
        '{"client_secret": "a.b.c"}',
        "eyJh.eyJw",
        "eyJh.eyJw.sig.extra",
    ],
)
def test_non_jwt_value_is_not_logged(secret):
    for value in (secret, f"Bearer {secret}"):
        assert str(TokenForLog(value)) == f"<non-jwt len={len(secret)}>"


@pytest.mark.parametrize("value", ["eyJh.eyJw.signature", "Bearer eyJh.eyJw.signature"])
def test_jwt_logs_header_and_payload_only(value):
    assert str(TokenForLog(value)) == "eyJh.eyJw"


def test_missing_token():
    assert str(TokenForLog(None)) == "None"


def test_trace_reports_call_site(caplog):
    logger = logging.getLogger("test.log_utils")
    with caplog.at_level(TRACE, logger=logger.name):
        trace(logger, "value=%s", 1)

    record = caplog.records[0]
    assert record.getMessage() == "value=1"
    assert record.levelno == TRACE
    assert Path(record.pathname) == Path(__file__)
    assert record.funcName == "test_trace_reports_call_site"


def test_token_is_not_formatted_when_trace_is_off():
    class Unformattable(str):
        def removeprefix(self, _prefix):
            raise AssertionError

    logger = logging.getLogger("test.log_utils.off")
    logger.setLevel(logging.DEBUG)
    trace(logger, "token=%s", TokenForLog(Unformattable("token")))


def test_level_name_is_not_registered():
    assert logging.getLevelName(TRACE) == f"Level {TRACE}"


@pytest.mark.parametrize(
    ("claims", "message"),
    [
        ({}, "Token missing exp claim"),
        ({"exp": 1}, "Token expired at"),
        ({"exp": time.time() + 3600, "scope": "storage.read"}, "Token has no WLCG or SciToken 2.0 version claim"),
        ({"exp": time.time() + 3600, "scope": "", "wlcg.ver": "1.0"}, "No acceptable scope found in token"),
    ],
)
def test_rejection_reason_is_logged_at_debug(caplog, monkeypatch, claims, message):
    monkeypatch.setattr(token_generator.SciToken, "deserialize", lambda _: claims)
    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"):
        valid, _ = token_is_valid_and_acceptable("eyJh.eyJw.sig", "/path", None, TokenOperation.TokenRead)

    assert not valid
    assert message in caplog.text
    assert "eyJh" not in caplog.text


def test_deserialize_failure_is_logged_at_debug(caplog):
    with caplog.at_level(logging.DEBUG, logger="fsspec.pelican"):
        valid, _ = token_is_valid_and_acceptable("opaque-secret", "/path", None, TokenOperation.TokenRead)

    assert not valid
    assert "Failed to deserialize token" in caplog.text
    assert "opaque-secret" not in caplog.text
