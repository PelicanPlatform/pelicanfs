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
import json
from unittest.mock import mock_open, patch

import pytest

from pelicanfs.token_content_iterator import TokenContentIterator, TokenDiscoveryMethod


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    # Fully isolate environment: clear all environment variables
    monkeypatch.setattr("os.environ", {}, raising=False)
    yield
    # monkeypatch automatically restores os.environ after the test


def test_next_uses_bearer_token_env(monkeypatch):
    monkeypatch.setenv("BEARER_TOKEN", "envtoken")
    iterator = TokenContentIterator(location=None, name="tokenname")
    token = next(iterator)
    assert token == "envtoken"


def test_next_fallback_to_bearer_token_file(monkeypatch, tmp_path):
    token_file = tmp_path / "tokenfile"
    token_file.write_text("filetoken")
    monkeypatch.delenv("BEARER_TOKEN", raising=False)
    monkeypatch.setenv("BEARER_TOKEN_FILE", str(token_file))
    iterator = TokenContentIterator(location=None, name="tokenname")
    token = next(iterator)
    assert token == "filetoken"


def test_discoverHTCondorTokenLocations(monkeypatch, tmp_path):
    condor_dir = tmp_path / ".condor_creds"
    condor_dir.mkdir()
    token_file = condor_dir / "token.use"
    token_file.write_text("tokendata")
    monkeypatch.setenv("_CONDOR_CREDS", str(condor_dir))
    iterator = TokenContentIterator(location=None, name="token")
    locations = iterator.discoverHTCondorTokenLocations("token")
    assert any(str(token_file) in loc for loc in locations)


@patch("os.path.exists", return_value=False)
@patch("os.access", return_value=False)
def test_explicit_location_unreadable_fallback(mock_access, mock_exists, monkeypatch):
    iterator = TokenContentIterator(location="/nonexistent/token", name="token_name")
    iterator.method = TokenDiscoveryMethod.LOCATION
    monkeypatch.setenv("BEARER_TOKEN", "fallback-token")
    token = next(iterator)
    assert token == "fallback-token"


def test_bearer_token_env_missing_fallback(monkeypatch):
    iterator = TokenContentIterator(location=None, name="token_name")
    iterator.method = TokenDiscoveryMethod.ENV_BEARER_TOKEN
    with pytest.raises(StopIteration):
        next(iterator)


@patch("os.path.exists", return_value=True)
@patch("os.access", return_value=False)
def test_bearer_token_file_unreadable(mock_access, mock_exists, monkeypatch):
    iterator = TokenContentIterator(location=None, name="token_name")
    iterator.method = TokenDiscoveryMethod.ENV_BEARER_TOKEN_FILE
    monkeypatch.setenv("BEARER_TOKEN_FILE", "/unreadable/token/file")
    with pytest.raises(StopIteration):
        next(iterator)


@patch("igwn_auth_utils.scitokens.default_bearer_token_file", return_value="/default/token/file")
@patch("os.path.exists", return_value=False)
def test_default_bearer_token_file_missing(mock_exists, mock_default_path):
    iterator = TokenContentIterator(location=None, name="token_name")
    iterator.method = TokenDiscoveryMethod.DEFAULT_BEARER_TOKEN
    with pytest.raises(StopIteration):
        next(iterator)


@patch("os.path.exists", return_value=False)
def test_token_env_file_missing(mock_exists, monkeypatch):
    iterator = TokenContentIterator(location=None, name="token_name")
    iterator.method = TokenDiscoveryMethod.ENV_TOKEN_PATH
    monkeypatch.setenv("TOKEN", "/nonexistent/token/file")
    with pytest.raises(StopIteration):
        next(iterator)


@patch("igwn_auth_utils.scitokens._find_condor_creds_token_paths", side_effect=FileNotFoundError)
def test_htcondor_creds_dir_missing(mock_find_paths):
    iterator = TokenContentIterator(location=None, name="token_name")
    iterator.method = TokenDiscoveryMethod.HTCONDOR_DISCOVERY
    with pytest.raises(StopIteration):
        next(iterator)


@patch("igwn_auth_utils.scitokens._find_condor_creds_token_paths", return_value=["/bad/token1.use", "/bad/token2.use"])
@patch("pelicanfs.token_content_iterator.get_token_from_file", side_effect=OSError("Unreadable file"))
def test_htcondor_creds_files_unreadable(mock_get_token, mock_find_paths):
    iterator = TokenContentIterator(location=None, name="token_name")
    iterator.method = TokenDiscoveryMethod.HTCONDOR_DISCOVERY
    with pytest.raises(StopIteration):
        next(iterator)  # All paths are unreadable, no token returned


@patch("os.path.exists", return_value=True)
@patch("os.access", return_value=True)
@patch("pelicanfs.token_content_iterator.get_token_from_file", return_value="valid-token")
def test_bearer_token_file_success(mock_get_token, mock_access, mock_exists, monkeypatch):
    iterator = TokenContentIterator(location=None, name="token_name")
    iterator.method = TokenDiscoveryMethod.ENV_BEARER_TOKEN_FILE
    monkeypatch.setenv("BEARER_TOKEN_FILE", "/valid/token/file")
    token = next(iterator)
    assert token == "valid-token"


@patch("os.path.exists", return_value=True)
@patch("os.access", return_value=True)
@patch("builtins.open", new_callable=mock_open, read_data='{"access_token": "xyz789"}')
def test_token_iterator_reads_valid_file(mock_open_func, mock_access, mock_exists):
    iterator = TokenContentIterator(location="/valid/token/file", name="token_name")
    iterator.method = TokenDiscoveryMethod.LOCATION
    token = next(iterator)
    assert token == "xyz789"


@patch("os.path.exists", return_value=True)
@patch("os.access", return_value=True)
@patch("pelicanfs.token_content_iterator.get_token_from_file", side_effect=json.JSONDecodeError("Expecting value", "", 0))
def test_token_iterator_handles_json_error(mock_get_token, mock_access, mock_exists):
    iterator = TokenContentIterator(location="/bad.json", name="token_name")
    iterator.method = TokenDiscoveryMethod.LOCATION
    with pytest.raises(StopIteration):
        next(iterator)


@patch("pelicanfs.token_content_iterator.TokenContentIterator.discoverHTCondorTokenLocations", return_value=["/bad/token1.use", "/good/token2.use"])
@patch("pelicanfs.token_content_iterator.get_token_from_file", side_effect=[OSError("Unreadable file"), "valid-fallback-token"])  # /bad/token1.use  # /good/token2.use
@patch("os.path.exists", return_value=True)
@patch("os.access", return_value=True)
def test_htcondor_creds_fallback_succeeds(mock_access, mock_exists, mock_get_token, mock_discover):
    iterator = TokenContentIterator(location=None, name="token_name")
    iterator.method = TokenDiscoveryMethod.HTCONDOR_DISCOVERY

    token = next(iterator)
    assert token == "valid-fallback-token"


@patch("igwn_auth_utils.scitokens._find_condor_creds_token_paths", return_value=["/bad/token1.use", "/bad/token2.use"])
@patch("pelicanfs.token_content_iterator.get_token_from_file", side_effect=OSError("Unreadable file"))
@patch("os.path.exists", return_value=True)
@patch("os.access", return_value=True)
def test_htcondor_fallback_all_fail_raises_stopiteration(mock_access, mock_exists, mock_get_token, mock_find_paths):
    iterator = TokenContentIterator(location=None, name="token_name")
    iterator.method = TokenDiscoveryMethod.HTCONDOR_DISCOVERY

    # First next(): triggers discovery and appends fallback
    with pytest.raises(StopIteration):
        while True:
            next(iterator)
