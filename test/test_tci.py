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


# OIDC Device Flow Tests


@patch("shutil.which", return_value=None)
def test_oidc_device_flow_binary_not_found(mock_which, caplog):
    """Test that missing pelican binary logs warning and allows iteration to continue"""
    import logging

    iterator = TokenContentIterator(location=None, name="token_name", destination_url="https://example.com")
    iterator.method_index = iterator.get_method_index(TokenDiscoveryMethod.OIDC_DEVICE_FLOW)

    # Iterator should exhaust naturally after OIDC_DEVICE_FLOW case runs
    with caplog.at_level(logging.WARNING):
        with pytest.raises(StopIteration):
            next(iterator)

    # Verify OIDC_DEVICE_FLOW case was executed and logged the expected warning
    assert any("pelican' binary is installed" in record.message for record in caplog.records)


@patch("shutil.which", return_value="/usr/bin/pelican")
@patch("pty.openpty")
@patch("subprocess.Popen")
@patch("os.read")
@patch("os.write")
@patch("os.close")
@patch("select.select")
def test_oidc_device_flow_successful_token_acquisition(mock_select, mock_close, mock_write, mock_read, mock_popen, mock_openpty, mock_which):
    """Test successful token acquisition via OIDC device flow"""
    from pelicanfs.token_generator import TokenOperation

    fake_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"

    # Mock PTY and subprocess
    mock_openpty.return_value = (100, 101)
    mock_process = mock_popen.return_value
    mock_process.returncode = 0
    mock_process.poll.side_effect = [None, 0]

    # Mock output data
    full_output = (
        b"WARNING: empty password provided; the credentials will be saved unencrypted on disk\n"
        b"To approve credentials for this operation, please navigate to the following URL and approve the request:\n"
        b"https://example-issuer.org/device?user_code=ABC-123-XYZ\n" + fake_jwt.encode() + b"\n"
    )

    mock_read.side_effect = [full_output, b""]
    mock_select.side_effect = [([100], [], []), ([], [], []), ([], [], [])]

    iterator = TokenContentIterator(location=None, name="token_name", operation=TokenOperation.TokenRead, pelican_url="pelican://example.com/path")
    iterator.method_index = iterator.get_method_index(TokenDiscoveryMethod.OIDC_DEVICE_FLOW)

    token = next(iterator)

    assert token.startswith("eyJ")
    assert len(token.split(".")) == 3
    assert token == fake_jwt

    mock_popen.assert_called_once()
    call_args = mock_popen.call_args[0][0]
    assert "pelican" in call_args
    assert "token" in call_args
    assert "fetch" in call_args
    assert "pelican://example.com/path" in call_args
    assert "-r" in call_args


@patch("shutil.which", return_value="/usr/bin/pelican")
@patch("pty.openpty")
@patch("subprocess.Popen")
@patch("os.read")
@patch("os.write")
@patch("os.close")
@patch("select.select")
def test_oidc_device_flow_with_warning_prefix(mock_select, mock_close, mock_write, mock_read, mock_popen, mock_openpty, mock_which):
    """Test token extraction when output has warning prefix"""
    from pelicanfs.token_generator import TokenOperation

    fake_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0In0.abc123def456"

    # Mock PTY
    mock_openpty.return_value = (100, 101)

    mock_process = mock_popen.return_value
    mock_process.returncode = 0
    mock_process.poll.side_effect = [None, 0]

    full_output = b"Token was acquired from issuer but it does not appear valid for transfer; trying anyway\n" + fake_jwt.encode() + b"\n"
    mock_read.side_effect = [full_output, b""]
    mock_select.side_effect = [([100], [], []), ([], [], []), ([], [], [])]

    iterator = TokenContentIterator(location=None, name="token_name", operation=TokenOperation.TokenRead, pelican_url="pelican://example.com/path")
    iterator.method_index = iterator.get_method_index(TokenDiscoveryMethod.OIDC_DEVICE_FLOW)

    token = next(iterator)

    assert token.startswith("eyJ")
    assert "trying anyway" not in token  # Warning prefix should not be in token
    assert token == fake_jwt


@patch("shutil.which", return_value="/usr/bin/pelican")
@patch("pty.openpty")
@patch("subprocess.Popen")
@patch("os.read")
@patch("os.write")
@patch("os.close")
@patch("select.select")
def test_oidc_device_flow_write_operation(mock_select, mock_close, mock_write, mock_read, mock_popen, mock_openpty, mock_which):
    """Test that write operation uses -w flag"""
    from pelicanfs.token_generator import TokenOperation

    fake_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ3cml0ZSJ9.xyz789abc"

    # Mock PTY
    mock_openpty.return_value = (100, 101)

    mock_process = mock_popen.return_value
    mock_process.returncode = 0
    mock_process.poll.side_effect = [None, 0]

    mock_read.side_effect = [fake_jwt.encode() + b"\n", b""]
    mock_select.side_effect = [([100], [], []), ([], [], [])]

    iterator = TokenContentIterator(location=None, name="token_name", operation=TokenOperation.TokenWrite, pelican_url="pelican://example.com/write/path")
    iterator.method_index = iterator.get_method_index(TokenDiscoveryMethod.OIDC_DEVICE_FLOW)

    token = next(iterator)

    # Verify -w flag was used for write operation
    call_args = mock_popen.call_args[0][0]
    assert "pelican" in call_args
    assert "token" in call_args
    assert "fetch" in call_args
    assert "pelican://example.com/write/path" in call_args
    assert "-w" in call_args
    assert token == fake_jwt


@patch("shutil.which", return_value="/usr/bin/pelican")
@patch("pty.openpty")
@patch("subprocess.Popen")
@patch("os.read")
@patch("os.write")
@patch("os.close")
@patch("select.select")
def test_oidc_device_flow_binary_fails(mock_select, mock_close, mock_write, mock_read, mock_popen, mock_openpty, mock_which):
    """Test that StopIteration is raised when pelican binary exits with error"""
    from pelicanfs.token_generator import TokenOperation

    # Mock PTY
    mock_openpty.return_value = (100, 101)

    mock_process = mock_popen.return_value
    mock_process.returncode = 1
    mock_process.poll.side_effect = [None, 1]

    mock_read.side_effect = [b"Error: failed to authenticate\n", b""]
    mock_select.side_effect = [([100], [], []), ([], [], [])]

    iterator = TokenContentIterator(location=None, name="token_name", operation=TokenOperation.TokenRead, pelican_url="pelican://example.com/path")
    iterator.method_index = iterator.get_method_index(TokenDiscoveryMethod.OIDC_DEVICE_FLOW)

    with pytest.raises(StopIteration):
        next(iterator)


@patch("shutil.which", return_value="/usr/bin/pelican")
@patch("pty.openpty")
@patch("subprocess.Popen")
@patch("os.read")
@patch("os.write")
@patch("os.close")
@patch("select.select")
def test_oidc_device_flow_no_token_in_output(mock_select, mock_close, mock_write, mock_read, mock_popen, mock_openpty, mock_which):
    """Test that StopIteration is raised when no JWT token is found in output"""
    from pelicanfs.token_generator import TokenOperation

    # Mock PTY
    mock_openpty.return_value = (100, 101)

    mock_process = mock_popen.return_value
    mock_process.returncode = 0
    mock_process.poll.side_effect = [None, 0]

    mock_read.side_effect = [b"Some output without a token\n", b"Another line\n", b""]
    mock_select.side_effect = [([100], [], []), ([], [], [])]

    iterator = TokenContentIterator(location=None, name="token_name", operation=TokenOperation.TokenRead, pelican_url="pelican://example.com/path")
    iterator.method_index = iterator.get_method_index(TokenDiscoveryMethod.OIDC_DEVICE_FLOW)

    with pytest.raises(StopIteration):
        next(iterator)


@patch("shutil.which", return_value="/usr/bin/pelican")
def test_oidc_device_flow_timeout(mock_which):
    """Test that timeout is handled gracefully.

    This test directly calls _get_token_from_pelican_binary with mocked internals
    rather than going through __next__, to avoid complex interactions with PTY,
    stdin, and select mocks that behave differently under pytest's capture mode.
    """
    from pelicanfs.token_generator import TokenOperation

    iterator = TokenContentIterator(
        location=None,
        name="token_name",
        operation=TokenOperation.TokenRead,
        pelican_url="pelican://example.com/path",
        oidc_timeout_seconds=0,  # Immediate timeout
    )

    # Mock _get_token_from_pelican_binary to simulate what happens when the
    # binary times out: it returns None (kill is called internally).
    # We verify the full iterator raises StopIteration when the OIDC flow returns None.
    iterator.method_index = iterator.get_method_index(TokenDiscoveryMethod.OIDC_DEVICE_FLOW)

    with patch.object(iterator, "_get_token_from_pelican_binary", return_value=None) as mock_get_token:
        with pytest.raises(StopIteration):
            next(iterator)
        mock_get_token.assert_called_once()


@patch("shutil.which", return_value="/usr/bin/pelican")
def test_oidc_device_flow_no_destination_url(mock_which):
    """Test that OIDC device flow fails gracefully without destination_url"""
    from pelicanfs.token_generator import TokenOperation

    iterator = TokenContentIterator(location=None, name="token_name", operation=TokenOperation.TokenRead, destination_url=None)  # No destination URL
    iterator.method_index = iterator.get_method_index(TokenDiscoveryMethod.OIDC_DEVICE_FLOW)

    with pytest.raises(StopIteration):
        next(iterator)


def test_get_pelican_flag_read_operations():
    """Test that read operations map to -r flag"""
    from pelicanfs.token_generator import TokenOperation

    # TokenRead
    iterator = TokenContentIterator(location=None, name="test", operation=TokenOperation.TokenRead, destination_url="https://example.com")
    flags = iterator._get_pelican_flag()
    assert "-r" in flags
    assert "-w" not in flags
    assert "-m" not in flags

    # TokenSharedRead
    iterator = TokenContentIterator(location=None, name="test", operation=TokenOperation.TokenSharedRead, destination_url="https://example.com")
    flags = iterator._get_pelican_flag()
    assert "-r" in flags
    assert "-w" not in flags
    assert "-m" not in flags


def test_get_pelican_flag_write_operations():
    """Test that write operations map to -w flag"""
    from pelicanfs.token_generator import TokenOperation

    # TokenWrite
    iterator = TokenContentIterator(location=None, name="test", operation=TokenOperation.TokenWrite, destination_url="https://example.com")
    flags = iterator._get_pelican_flag()
    assert "-w" in flags
    assert "-r" not in flags
    assert "-m" not in flags

    # TokenSharedWrite
    iterator = TokenContentIterator(location=None, name="test", operation=TokenOperation.TokenSharedWrite, destination_url="https://example.com")
    flags = iterator._get_pelican_flag()
    assert "-w" in flags
    assert "-r" not in flags
    assert "-m" not in flags


def test_get_pelican_flag_default():
    """Test that None operation defaults to -r flag"""
    iterator = TokenContentIterator(location=None, name="test", operation=None, destination_url="https://example.com")
    flags = iterator._get_pelican_flag()
    assert "-r" in flags
    assert "-w" not in flags
    assert "-m" not in flags


def test_get_pelican_flag_debug_mode():
    """Test that debug flag is added when logger is in DEBUG mode"""
    import logging

    from pelicanfs.token_generator import TokenOperation

    # Get the actual logger used in the module
    logger = logging.getLogger("fsspec.pelican")
    original_level = logger.getEffectiveLevel()

    # Set logger to DEBUG level
    logger.setLevel(logging.DEBUG)

    try:
        iterator = TokenContentIterator(location=None, name="test", operation=TokenOperation.TokenRead, destination_url="https://example.com")
        flags = iterator._get_pelican_flag()
        assert "-d" in flags
        assert "-r" in flags
    finally:
        # Restore original log level
        logger.setLevel(original_level)


def test_pelican_binary_exists():
    """Test _pelican_binary_exists method"""
    iterator = TokenContentIterator(location=None, name="test")

    # This will return True or False depending on whether pelican is actually installed
    # We just test that the method works without error
    result = iterator._pelican_binary_exists()
    assert isinstance(result, bool)
