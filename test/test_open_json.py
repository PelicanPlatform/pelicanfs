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
from unittest.mock import mock_open

import pytest

from pelicanfs.token_content_iterator import get_token_from_file


def test_get_token_from_file_json_without_access_token(monkeypatch):
    token_data = '{"some_other_key": "value"}'

    monkeypatch.setattr("builtins.open", mock_open(read_data=token_data))
    token = get_token_from_file("/fake/path")
    assert token == token_data  # fallback to raw token string


def test_get_token_from_file_json_with_access_token(monkeypatch):
    token_data = '{"access_token": "abc123"}'

    monkeypatch.setattr("builtins.open", mock_open(read_data=token_data))
    token = get_token_from_file("/fake/path")
    assert token == "abc123"


def test_get_token_from_file_invalid_json(monkeypatch):
    bad_json = "{not valid json"

    monkeypatch.setattr("builtins.open", mock_open(read_data=bad_json))
    token = get_token_from_file("/fake/path")
    assert token == bad_json.strip()  # fallback to raw token string


def test_get_token_from_file_plain_token(monkeypatch):
    token_data = "plain-token-value"

    monkeypatch.setattr("builtins.open", mock_open(read_data=token_data))
    token = get_token_from_file("/fake/path")
    assert token == "plain-token-value"


def test_get_token_from_file_io_error(monkeypatch):
    def raise_io(*args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr("builtins.open", raise_io)
    with pytest.raises(OSError):
        get_token_from_file("/fake/path")
