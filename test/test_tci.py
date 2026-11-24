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

import contextlib
import json
import os
import subprocess
import sys
import time
from unittest.mock import MagicMock, mock_open, patch

import pytest

import pelicanfs.auth_handler
from pelicanfs import set_auth_handler
from pelicanfs import token_content_iterator as tci
from pelicanfs.token_content_iterator import TokenContentIterator, TokenDiscoveryMethod, _TerminalEcho, _VtFilter

needs_pty = pytest.mark.skipif(sys.platform == "win32", reason="Tests the Unix pty code path")
needs_conpty = pytest.mark.skipif(sys.platform != "win32", reason="Needs a real ConPTY")


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    # Fully isolate environment, keeping SYSTEMROOT, which Windows needs
    env = {"SYSTEMROOT": os.environ["SYSTEMROOT"]} if "SYSTEMROOT" in os.environ else {}
    monkeypatch.setattr("os.environ", env, raising=False)


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
    with pytest.raises(StopIteration):  # noqa: PT012
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
    with caplog.at_level(logging.WARNING), pytest.raises(StopIteration):
        next(iterator)

    # Verify OIDC_DEVICE_FLOW case was executed and logged the expected warning
    assert any("pelican' binary is installed" in record.message for record in caplog.records)


@needs_pty
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


@needs_pty
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


@needs_pty
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


@needs_pty
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


@needs_pty
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


FAKE_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
PELICAN_PREAMBLE = (
    b"The client is able to save the authorization in a local file.\n"
    b"This prevents the need to reinitialize the authorization for each transfer.\n"
    b"You will be asked for this password whenever a new session is started.\n"
)
# pelican 7.23 wording; the prompt has no trailing newline.
PELICAN_PROMPT = b"Please provide a new password to encrypt the local OSDF client configuration file:"
# Current pelican wording (config/encrypted.go); the prompt is written with Fprintln.
PELICAN_PROMPT_LN = b"Please create a new password to protect your local credentials: \n"
PELICAN_URL_LINE = b"To approve credentials for this operation, please navigate to the following URL and approve the request:\nhttps://issuer.example/device?user_code=ABC-123\n"


def _jupyter_iterator(**kwargs):
    from pelicanfs.token_generator import TokenOperation

    iterator = TokenContentIterator(location=None, name="token_name", operation=TokenOperation.TokenRead, pelican_url="pelican://example.com/path", **kwargs)
    iterator.method_index = iterator.get_method_index(TokenDiscoveryMethod.OIDC_DEVICE_FLOW)
    return iterator


def _select_for(chunks):
    """Signal readable once per chunk, then idle."""
    return [([100], [], [])] * len(chunks) + [([], [], [])] * 3


@contextlib.contextmanager
def _mock_pty(in_kernel):
    """Mock the pty machinery; stdin must come from the pty, not from /dev/tty."""
    with (
        patch("shutil.which", return_value="/usr/bin/pelican"),
        patch("pelicanfs.token_content_iterator._running_in_ipykernel", return_value=in_kernel),
        patch("pty.openpty", return_value=(100, 101)),
        patch("termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, []]),
        patch("termios.tcsetattr"),
        patch("subprocess.Popen") as mock_popen,
        patch("os.read") as mock_read,
        patch("os.write") as mock_write,
        patch("os.close"),
        patch("os.open") as mock_open_fd,
        patch("select.select") as mock_select,
        patch("getpass.getpass", return_value="hunter2") as mock_getpass,
    ):
        mock_open_fd.side_effect = AssertionError("/dev/tty must not be opened when a handler answers the prompts")
        process = mock_popen.return_value
        process.returncode = 0

        def feed(chunks, returncode=0):
            chunks = list(chunks)
            mock_read.side_effect = [*chunks, b""]
            mock_select.side_effect = _select_for(chunks)
            process.poll.return_value = returncode
            process.returncode = returncode

        yield {"feed": feed, "popen": mock_popen, "process": process, "write": mock_write, "getpass": mock_getpass, "select": mock_select}


@pytest.fixture
def jupyter_pty(monkeypatch):
    """Mock the pty machinery and pretend to run inside an IPython kernel."""
    import types

    displayed = []
    fake_display_module = types.ModuleType("IPython.display")
    fake_display_module.display = lambda data, raw=False: displayed.append((data, raw))
    monkeypatch.setitem(sys.modules, "IPython", types.ModuleType("IPython"))
    monkeypatch.setitem(sys.modules, "IPython.display", fake_display_module)
    with _mock_pty(in_kernel=True) as mocks:
        yield {**mocks, "displayed": displayed}


class RecordingHandler:
    def __init__(self, password="s3cret"):
        self.password = password
        self.shown = []
        self.prompts = []
        self.cancel = False
        self.finished = []

    def show(self, text, url=None):
        self.shown.append((text, url))

    def ask_password(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.password, BaseException):
            raise self.password
        return self.password

    def finish(self, success):
        self.finished.append(success)

    def cancelled(self):
        return self.cancel


@pytest.fixture
def handler_pty():
    """Mock the pty machinery with a registered auth handler, outside a kernel."""
    handler = RecordingHandler()
    previous = set_auth_handler(handler)
    try:
        with _mock_pty(in_kernel=False) as mocks:
            yield {**mocks, "handler": handler}
    finally:
        set_auth_handler(previous)


@needs_pty
def test_auth_handler_gets_output_and_answers_prompt(handler_pty, capsys):
    handler = handler_pty["handler"]
    handler_pty["feed"]([PELICAN_PREAMBLE + PELICAN_PROMPT, b"\r\n" + PELICAN_URL_LINE + FAKE_JWT.encode() + b"\n"])

    assert next(_jupyter_iterator()) == FAKE_JWT
    assert handler.prompts == [PELICAN_PROMPT.decode()]
    handler_pty["write"].assert_called_once_with(100, b"s3cret\n")
    handler_pty["getpass"].assert_not_called()
    assert handler_pty["popen"].call_args.kwargs["stdin"] == 101
    url = "https://issuer.example/device?user_code=ABC-123"
    assert (url, url) in handler.shown
    assert ("You will be asked for this password whenever a new session is started.", None) in handler.shown
    assert not any(FAKE_JWT_SIGNATURE in text for text, _ in handler.shown)
    assert capsys.readouterr().out == ""


@needs_pty
def test_auth_handler_wins_inside_kernel(jupyter_pty, capsys):
    handler = RecordingHandler()
    previous = set_auth_handler(handler)
    try:
        jupyter_pty["feed"]([PELICAN_PROMPT_LN, PELICAN_URL_LINE + FAKE_JWT.encode() + b"\n"])
        assert next(_jupyter_iterator()) == FAKE_JWT
    finally:
        set_auth_handler(previous)

    assert len(handler.prompts) == 1
    jupyter_pty["getpass"].assert_not_called()
    assert jupyter_pty["displayed"] == []
    assert capsys.readouterr().out == ""


@needs_pty
def test_auth_handler_error_stops_the_flow(handler_pty):
    handler_pty["handler"].password = RuntimeError("cancelled")
    handler_pty["feed"]([PELICAN_PROMPT_LN])
    handler_pty["process"].poll.return_value = None
    handler_pty["process"].returncode = None

    assert _jupyter_iterator()._get_token_from_pelican_binary() is None
    handler_pty["process"].kill.assert_called_once()
    handler_pty["write"].assert_not_called()


@needs_pty
def test_auth_handler_finish_after_token(handler_pty):
    handler_pty["feed"]([FAKE_JWT.encode() + b"\n"])

    assert next(_jupyter_iterator()) == FAKE_JWT
    assert handler_pty["handler"].finished == [True]


@needs_pty
def test_auth_handler_finish_after_failure(handler_pty):
    handler_pty["feed"]([b"error: nope\n"], returncode=1)

    assert _jupyter_iterator()._get_token_from_pelican_binary() is None
    assert handler_pty["handler"].finished == [False]


@needs_pty
def test_auth_handler_finish_after_handler_error(handler_pty):
    handler = handler_pty["handler"]
    handler.password = RuntimeError("cancelled")
    handler_pty["feed"]([PELICAN_PROMPT_LN])

    assert _jupyter_iterator()._get_token_from_pelican_binary() is None
    assert handler.finished == [False]


@needs_pty
def test_auth_handler_finish_error_keeps_token(handler_pty):
    handler_pty["handler"].finish = MagicMock(side_effect=RuntimeError("app is gone"))
    handler_pty["feed"]([FAKE_JWT.encode() + b"\n"])

    assert next(_jupyter_iterator()) == FAKE_JWT


@needs_pty
def test_auth_handler_cancelled_stops_pelican(handler_pty):
    handler = handler_pty["handler"]
    handler.cancel = True
    handler_pty["feed"]([b"waiting...\n"])
    process = handler_pty["process"]
    process.poll.return_value = None
    process.returncode = None

    assert _jupyter_iterator()._get_token_from_pelican_binary() is None
    process.kill.assert_called_once()
    assert handler.finished == [False]
    assert handler.shown == []


@pytest.mark.parametrize("missing", ["show", "ask_password", "finish", "cancelled"])
def test_set_auth_handler_rejects_incomplete_handler(missing):
    handler = RecordingHandler()
    setattr(handler, missing, None)

    with pytest.raises(TypeError, match=missing):
        set_auth_handler(handler)
    assert pelicanfs.auth_handler.get_auth_handler() is None


def test_set_auth_handler_returns_previous():
    first, second = RecordingHandler(), RecordingHandler()
    assert set_auth_handler(first) is None
    assert set_auth_handler(second) is first
    assert set_auth_handler(None) is second
    assert pelicanfs.auth_handler.get_auth_handler() is None


@needs_pty
def test_oidc_jupyter_password_prompt(jupyter_pty, capsys):
    jupyter_pty["feed"]([PELICAN_PREAMBLE + PELICAN_PROMPT, b"\n" + PELICAN_URL_LINE + FAKE_JWT.encode() + b"\n"])

    token = next(_jupyter_iterator())

    assert token == FAKE_JWT
    jupyter_pty["getpass"].assert_called_once()
    jupyter_pty["write"].assert_called_once_with(100, b"hunter2\n")
    popen_kwargs = jupyter_pty["popen"].call_args.kwargs
    assert popen_kwargs["stdin"] == 101
    out = capsys.readouterr().out
    assert "configuration file:" in out
    assert "hunter2" not in out
    assert FAKE_JWT not in out
    # The token line is dropped, and the URL is shown once (by display, not print).
    assert "[SIGNATURE_REDACTED]" not in out
    assert "https://issuer.example/device?user_code=ABC-123" not in out
    assert len(jupyter_pty["displayed"]) == 1


@needs_pty
def test_oidc_jupyter_password_prompt_with_newline(jupyter_pty, capsys):
    jupyter_pty["feed"]([PELICAN_PROMPT_LN, b"\n" + FAKE_JWT.encode() + b"\n"])

    assert next(_jupyter_iterator()) == FAKE_JWT
    jupyter_pty["getpass"].assert_called_once()
    jupyter_pty["write"].assert_called_once_with(100, b"hunter2\n")
    assert "local credentials:" in capsys.readouterr().out


@needs_pty
def test_oidc_jupyter_prose_password_line_does_not_prompt(jupyter_pty):
    jupyter_pty["feed"]([PELICAN_PREAMBLE, FAKE_JWT.encode() + b"\n"])

    token = next(_jupyter_iterator())

    assert token == FAKE_JWT
    jupyter_pty["getpass"].assert_not_called()
    jupyter_pty["write"].assert_not_called()


@needs_pty
def test_oidc_jupyter_prompt_split_across_reads(jupyter_pty):
    jupyter_pty["feed"]([b"Please provide a new pass", b"word to encrypt the file:", b"\n" + FAKE_JWT.encode() + b"\n"])

    token = next(_jupyter_iterator())

    assert token == FAKE_JWT
    jupyter_pty["getpass"].assert_called_once()


@needs_pty
def test_oidc_jupyter_url_is_displayed(jupyter_pty, capsys):
    jupyter_pty["feed"]([PELICAN_URL_LINE + FAKE_JWT.encode() + b"\n"])

    assert next(_jupyter_iterator()) == FAKE_JWT
    assert "https://issuer.example" not in capsys.readouterr().out
    assert len(jupyter_pty["displayed"]) == 1
    data, raw = jupyter_pty["displayed"][0]
    assert raw is True
    assert data["text/plain"] == "https://issuer.example/device?user_code=ABC-123"
    assert 'href="https://issuer.example/device?user_code=ABC-123"' in data["text/html"]
    assert 'target="_blank"' in data["text/html"]


@needs_pty
def test_oidc_jupyter_url_falls_back_to_print_without_ipython(jupyter_pty, monkeypatch, capsys):
    monkeypatch.delitem(sys.modules, "IPython.display")
    jupyter_pty["feed"]([PELICAN_URL_LINE + FAKE_JWT.encode() + b"\n"])

    assert next(_jupyter_iterator()) == FAKE_JWT
    assert capsys.readouterr().out.count("https://issuer.example/device?user_code=ABC-123") == 1
    assert jupyter_pty["displayed"] == []


FAKE_JWT_SIGNATURE = FAKE_JWT.rpartition(".")[2]
REDACTED_JWT = f"{FAKE_JWT.rpartition('.')[0]}.[SIGNATURE_REDACTED]"
TOKEN_URL_LINE = f"Go to https://issuer.example/device?token={FAKE_JWT} ({FAKE_JWT})\n".encode()


@needs_pty
def test_oidc_jupyter_url_line_is_redacted(jupyter_pty):
    jupyter_pty["feed"]([TOKEN_URL_LINE + FAKE_JWT.encode() + b"\n"])

    assert next(_jupyter_iterator()) == FAKE_JWT
    data, _ = jupyter_pty["displayed"][0]
    assert FAKE_JWT_SIGNATURE not in data["text/plain"]
    assert FAKE_JWT_SIGNATURE not in data["text/html"]
    assert REDACTED_JWT in data["text/plain"]
    assert f'href="https://issuer.example/device?token={REDACTED_JWT}"' in data["text/html"]


@needs_pty
def test_oidc_jupyter_url_line_is_redacted_without_ipython(jupyter_pty, monkeypatch, capsys):
    monkeypatch.delitem(sys.modules, "IPython.display")
    jupyter_pty["feed"]([TOKEN_URL_LINE + FAKE_JWT.encode() + b"\n"])

    assert next(_jupyter_iterator()) == FAKE_JWT
    out = capsys.readouterr().out
    assert FAKE_JWT_SIGNATURE not in out
    assert REDACTED_JWT in out


@needs_pty
def test_oidc_jupyter_interrupt_stops_pelican(jupyter_pty):
    jupyter_pty["feed"]([PELICAN_PROMPT_LN])
    process = jupyter_pty["process"]
    process.poll.return_value = None
    process.returncode = None
    jupyter_pty["getpass"].side_effect = KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _jupyter_iterator()._get_token_from_pelican_binary()
    process.kill.assert_called_once()
    process.wait.assert_called()


@needs_pty
def test_oidc_jupyter_error_falls_through(jupyter_pty):
    jupyter_pty["feed"]([PELICAN_PROMPT_LN])
    jupyter_pty["getpass"].side_effect = OSError("stdin is not available")

    with pytest.raises(StopIteration):
        next(_jupyter_iterator())


@needs_pty
def test_oidc_jupyter_timeout(jupyter_pty):
    jupyter_pty["feed"]([b"waiting...\n"])
    jupyter_pty["process"].poll.return_value = None
    jupyter_pty["process"].returncode = None

    iterator = _jupyter_iterator(oidc_timeout_seconds=0)
    assert iterator._get_token_from_pelican_binary() is None
    jupyter_pty["process"].kill.assert_called_once()


@needs_pty
def test_oidc_jupyter_nonzero_exit(jupyter_pty):
    jupyter_pty["feed"]([b"error: nope\n"], returncode=1)

    assert _jupyter_iterator()._get_token_from_pelican_binary() is None


@needs_pty
@patch("shutil.which", return_value="/usr/bin/pelican")
@patch("pelicanfs.token_content_iterator._running_in_ipykernel", return_value=False)
@patch("pty.openpty", return_value=(100, 101))
@patch("subprocess.Popen")
@patch("os.read")
@patch("os.write")
@patch("os.close")
@patch("os.open", return_value=7)
@patch("select.select")
@patch("getpass.getpass")
def test_oidc_terminal_path_unchanged_outside_kernel(mock_getpass, mock_select, mock_open_fd, mock_close, mock_write, mock_read, mock_popen, mock_openpty, mock_kernel, mock_which):
    process = mock_popen.return_value
    process.returncode = 0
    process.poll.side_effect = [None, 0]
    mock_read.side_effect = [PELICAN_PROMPT + b"\n" + FAKE_JWT.encode() + b"\n", b""]
    mock_select.side_effect = _select_for([1])

    assert next(_jupyter_iterator()) == FAKE_JWT
    mock_open_fd.assert_called_once_with("/dev/tty", os.O_RDWR)
    assert mock_popen.call_args.kwargs["stdin"] == 7
    mock_getpass.assert_not_called()


@needs_pty
@pytest.mark.parametrize("split", [1, 2, 5, FAKE_JWT.index(".") + 1, FAKE_JWT.rindex("."), FAKE_JWT.rindex(".") + 1, len(FAKE_JWT) - 5, len(FAKE_JWT) - 1])
@patch("shutil.which", return_value="/usr/bin/pelican")
@patch("pelicanfs.token_content_iterator._running_in_ipykernel", return_value=False)
@patch("pty.openpty", return_value=(100, 101))
@patch("subprocess.Popen")
@patch("os.read")
@patch("os.write")
@patch("os.close")
@patch("os.open", return_value=7)
@patch("select.select")
def test_oidc_terminal_redacts_token_split_across_reads(mock_select, mock_open_fd, mock_close, mock_write, mock_read, mock_popen, mock_openpty, mock_kernel, mock_which, split, capsys):
    process = mock_popen.return_value
    process.returncode = 0
    process.poll.side_effect = [None, None, 0]
    mock_read.side_effect = [b"Token: " + FAKE_JWT[:split].encode(), FAKE_JWT[split:].encode() + b"\n", b""]
    mock_select.side_effect = _select_for([1, 2])

    assert next(_jupyter_iterator()) == FAKE_JWT
    out = capsys.readouterr().out
    signature = FAKE_JWT.rpartition(".")[2]
    assert out.startswith("Token: ")
    assert signature[:5] not in out
    assert out.rstrip().endswith("REDACTED]")


def test_terminal_echo_holds_only_a_possible_token(capsys):
    echo = _TerminalEcho()

    echo("Password: ")
    assert capsys.readouterr().out == "Password: "

    echo("The code")
    assert capsys.readouterr().out == "The cod"

    echo(" is 123\n")
    assert capsys.readouterr().out == "e is 123\n"

    echo("last line e")
    echo.flush()
    assert capsys.readouterr().out == "last line e"


def test_running_in_ipykernel_detection(monkeypatch):
    import types

    from pelicanfs.token_content_iterator import _running_in_ipykernel

    monkeypatch.delitem(sys.modules, "IPython", raising=False)
    assert _running_in_ipykernel() is False

    fake_ipython = types.ModuleType("IPython")
    monkeypatch.setitem(sys.modules, "IPython", fake_ipython)
    zmq_shell = type("ZMQInteractiveShell", (), {})
    colab_shell = type("Shell", (zmq_shell,), {})  # Colab and Spyder subclass the kernel shell
    for shell, expected in [(None, False), (type("TerminalInteractiveShell", (), {})(), False), (zmq_shell(), True), (colab_shell(), True)]:
        fake_ipython.get_ipython = MagicMock(return_value=shell)
        assert _running_in_ipykernel() is expected


def _windows_branch_iterator(monkeypatch, script, timeout):
    """Run the Windows code path with a Python child in place of the pelican binary."""
    real_popen = subprocess.Popen

    def fake_popen(_cmd, **kwargs):
        return real_popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr("pelicanfs.token_content_iterator._IS_WINDOWS", True)
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    return TokenContentIterator(location=None, name="token_name", pelican_url="pelican://example.com/path", oidc_timeout_seconds=timeout)


def test_oidc_windows_branch_timeout_with_silent_child(monkeypatch):
    iterator = _windows_branch_iterator(monkeypatch, "import time; time.sleep(8)", timeout=1)

    start = time.monotonic()
    assert iterator._get_token_from_pelican_binary() is None
    assert time.monotonic() - start < 5


def test_oidc_windows_branch_returns_token(monkeypatch):
    script = "print('Navigate to https://example.org/device'); print('eyJa.eyJb.sigc')"
    iterator = _windows_branch_iterator(monkeypatch, script, timeout=30)

    assert iterator._get_token_from_pelican_binary() == "eyJa.eyJb.sigc"


def test_vt_filter_removes_escape_sequences():
    clean = _VtFilter()

    assert clean("\x1b[?25l\x1b[2J\x1b[m\x1b[H\x1b]0;C:\\pelican.exe\x07Password:\x1b[K\r\n") == "Password:\r\n"
    assert clean("a\x1b[3Cb\x1b[Cc\x1b]0;title\x1b\\d\x1b=e") == "a   b cde"


@pytest.mark.parametrize("text", ["ab\x1b[38;5;1mcd\x1b]0;title\x07ef\x1b[2Cgh"])
def test_vt_filter_sequence_split_across_reads(text):
    for split in range(len(text) + 1):
        clean = _VtFilter()
        assert clean(text[:split]) + clean(text[split:]) == "abcdef  gh"


class FakeConPty:
    """The part of winpty.PTY that the ConPTY code path uses."""

    pid = 4242

    def __init__(self, chunks, status=0, alive=False):
        self.chunks = list(chunks)
        self.status = status
        self.alive = alive
        self.written = []

    def read(self):
        return self.chunks.pop(0) if self.chunks else ""

    def write(self, text):
        self.written.append(text)

    def iseof(self):
        return not self.alive and not self.chunks

    def isalive(self):
        return self.alive

    def get_exitstatus(self):
        return None if self.alive else self.status


@pytest.fixture
def fake_conpty(monkeypatch):
    """Run the ConPTY code path on a fake PTY, with a registered auth handler."""
    import types

    handler = RecordingHandler()
    previous = set_auth_handler(handler)

    def run(pty):
        monkeypatch.setattr(tci, "_IS_WINDOWS", True)
        monkeypatch.setattr(tci, "winpty", types.SimpleNamespace(WinptyError=RuntimeError), raising=False)
        monkeypatch.setattr(tci, "_spawn_conpty", lambda _cmd: pty)
        return _jupyter_iterator(select_timeout=0.01)._get_token_from_pelican_binary()

    try:
        yield {"run": run, "handler": handler}
    finally:
        set_auth_handler(previous)


def test_conpty_handler_gets_clean_output_and_answers_prompt(fake_conpty):
    handler = fake_conpty["handler"]
    pty = FakeConPty(
        [
            "\x1b[?25l\x1b[2J\x1b[m\x1b[H" + PELICAN_PROMPT_LN.decode().replace("\n", "\r\n"),
            "\x1b]0;C:\\pelican.exe\x07\x1b[?25hhttps://issuer.example/device?user_code=ABC-123\r\n" + FAKE_JWT[:50],
            FAKE_JWT[50:] + "\x1b[",
            "K\r\n",
        ]
    )

    assert fake_conpty["run"](pty) == FAKE_JWT
    assert pty.written == ["s3cret\r"]
    assert handler.prompts == [PELICAN_PROMPT_LN.decode().rstrip("\n")]
    url = "https://issuer.example/device?user_code=ABC-123"
    assert handler.shown == [(url, url)]
    assert handler.finished == [True]


def test_conpty_nonzero_exit(fake_conpty):
    assert fake_conpty["run"](FakeConPty([FAKE_JWT + "\r\n"], status=1)) is None
    assert fake_conpty["handler"].finished == [False]


def test_conpty_cancelled_stops_pelican(fake_conpty, monkeypatch):
    import signal

    killed = []
    pty = FakeConPty([], alive=True)

    def kill(pid, sig):
        killed.append((pid, sig))
        pty.alive = False

    monkeypatch.setattr("os.kill", kill)
    fake_conpty["handler"].cancel = True

    assert fake_conpty["run"](pty) is None
    assert killed == [(4242, signal.SIGTERM)]
    assert fake_conpty["handler"].finished == [False]


# In place of pelican: needs a console for stdin, reads the password with no echo.
CONPTY_CHILD = """
import getpass
import sys

if not sys.stdin.isatty():
    sys.exit(2)
sys.stderr.write("Please create a new password to protect your local credentials: \\n")
sys.stderr.flush()
if getpass.getpass("") != "s3cret":
    sys.exit(3)
print("To approve credentials for this operation, please navigate to the following URL and approve the request:")
print("https://issuer.example/device?user_code=ABC-123")
print("{jwt}")
sys.exit({status})
"""
LONG_JWT = f"eyJ{'a' * 2000}.eyJ{'b' * 2000}.{'c' * 2000}"


@pytest.fixture
def conpty(monkeypatch, tmp_path):
    """Run the ConPTY code path with a Python child in place of the pelican binary."""
    handler = RecordingHandler()
    previous = set_auth_handler(handler)
    spawn = tci._spawn_conpty

    def run(script, **kwargs):
        path = tmp_path / "child.py"
        path.write_text(script)
        monkeypatch.setattr(tci, "_spawn_conpty", lambda _cmd: spawn([sys.executable, str(path)]))
        return _jupyter_iterator(**kwargs)._get_token_from_pelican_binary()

    try:
        yield {"run": run, "handler": handler}
    finally:
        set_auth_handler(previous)


@needs_conpty
def test_conpty_real_child_gets_password_and_returns_token(conpty):
    handler = conpty["handler"]

    assert conpty["run"](CONPTY_CHILD.format(jwt=LONG_JWT, status=0)) == LONG_JWT
    assert handler.prompts == ["Please create a new password to protect your local credentials: "]
    url = "https://issuer.example/device?user_code=ABC-123"
    assert (url, url) in handler.shown
    assert ("To approve credentials for this operation, please navigate to the following URL and approve the request:", None) in handler.shown
    assert not any("\x1b" in text or "s3cret" in text or "c" * 100 in text for text, _ in handler.shown)
    assert handler.finished == [True]


@needs_conpty
def test_conpty_real_child_token_longer_than_the_console_width(conpty, monkeypatch):
    monkeypatch.setattr(tci, "CONPTY_COLUMNS", 80)

    assert conpty["run"](CONPTY_CHILD.format(jwt=FAKE_JWT, status=0)) == FAKE_JWT


@needs_conpty
def test_conpty_real_child_wrong_password(conpty):
    conpty["handler"].password = "wrong"

    assert conpty["run"](CONPTY_CHILD.format(jwt=FAKE_JWT, status=0)) is None
    assert conpty["handler"].finished == [False]


@needs_conpty
def test_conpty_real_child_nonzero_exit(conpty):
    assert conpty["run"](CONPTY_CHILD.format(jwt=FAKE_JWT, status=1)) is None
    assert conpty["handler"].finished == [False]


@needs_conpty
def test_conpty_real_child_timeout_with_silent_child(conpty):
    start = time.monotonic()
    assert conpty["run"]("import time; time.sleep(30)", oidc_timeout_seconds=1) is None
    assert time.monotonic() - start < 10
    assert conpty["handler"].finished == [False]


@needs_conpty
def test_conpty_real_child_cancelled(conpty):
    handler = conpty["handler"]
    polls = []

    def cancelled():
        polls.append(time.monotonic())
        return len(polls) > 5

    handler.cancelled = cancelled
    start = time.monotonic()
    assert conpty["run"]("import time; time.sleep(30)") is None
    assert time.monotonic() - start < 10
    assert handler.finished == [False]
