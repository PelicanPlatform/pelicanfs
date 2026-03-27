"""
Tests for OIDC device flow and Jupyter notebook integration.

These tests mock the pelican binary interaction via pexpect to verify:
1. Password prompting works correctly
2. OIDC device flow URLs are displayed (with Jupyter HTML when available)
3. Token extraction from pelican binary output
4. Error handling for timeouts and failures
"""
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from scitokens import SciToken

from pelicanfs.token_content_iterator import TokenContentIterator, TokenDiscoveryMethod
from pelicanfs.token_generator import TokenOperation


def generate_test_token(issuer="https://test-issuer.example.com", scopes=None, lifetime=3600):
    """Generate a valid JWT token for testing.

    Returns a string (decoded from bytes) since that's what the pelican binary outputs.
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    token = SciToken(key=private_pem, algorithm="ES256")
    token["iss"] = issuer
    token["aud"] = "https://test.example.com"
    token["scope"] = " ".join(scopes) if scopes else "storage.read:/"

    # Decode to string since pelican binary outputs text
    return token.serialize(issuer=issuer, lifetime=lifetime).decode("utf-8")


class MockPexpectChild:
    """Mock pexpect child process for simulating pelican binary interaction."""

    def __init__(self, interactions, exit_status=0):
        """
        Args:
            interactions: List of tuples defining the interaction sequence.
                Each tuple is (pattern_index, before_text, after_text)
                where pattern_index maps to:
                    0 = password prompt
                    1 = OIDC URL
                    2 = EOF
                    3 = TIMEOUT
            exit_status: The exit status code to return
        """
        self.interactions = list(interactions)
        self.interaction_index = 0
        self.before = ""
        self.after = ""
        self.exitstatus = exit_status
        self.password_received = None
        self._closed = False

    def expect(self, patterns):
        if self.interaction_index >= len(self.interactions):
            # Simulate EOF when no more interactions
            raise EOFError("End of interactions")

        interaction = self.interactions[self.interaction_index]
        self.interaction_index += 1

        pattern_index, before, after = interaction
        self.before = before
        self.after = after
        return pattern_index

    def sendline(self, text):
        """Capture password input."""
        self.password_received = text

    def close(self, force=False):
        self._closed = True


class MockEOF(Exception):
    """Mock EOF exception."""

    pass


class MockTIMEOUT(Exception):
    """Mock TIMEOUT exception."""

    pass


@pytest.fixture
def mock_pexpect():
    """Create a mock pexpect module."""
    mock_module = MagicMock()
    mock_module.EOF = MockEOF
    mock_module.TIMEOUT = MockTIMEOUT
    return mock_module


@pytest.fixture
def iterator_factory():
    """Factory to create TokenContentIterator instances."""

    def factory(pelican_url="pelican://test.example.com/namespace/file.txt", operation=None, oidc_timeout_seconds=300):
        return TokenContentIterator(location=None, name="test_token", operation=operation, pelican_url=pelican_url, oidc_timeout_seconds=oidc_timeout_seconds)  # Required field

    return factory


class TestOIDCDeviceFlow:
    """Test OIDC device flow via pelican binary."""

    def test_password_prompt_handling(self, mock_pexpect, iterator_factory):
        """Test that password prompts are handled correctly."""
        test_token = generate_test_token()

        # Define interaction sequence:
        # 1. Password prompt appears
        # 2. After password, token is output and EOF
        mock_child = MockPexpectChild(
            [
                (0, "Connecting to server...\n", "Password: "),  # Password prompt
                (2, f"\nAuthenticated successfully.\nToken: {test_token}\n", ""),  # EOF with token
            ]
        )

        mock_pexpect.spawn = MagicMock(return_value=mock_child)
        mock_getpass = MagicMock(return_value="test_password_123")

        with patch.dict(sys.modules, {"pexpect": mock_pexpect}):
            with patch("pelicanfs.token_content_iterator._PEXPECT_AVAILABLE", True):
                with patch("pelicanfs.token_content_iterator.pexpect", mock_pexpect):
                    with patch("getpass.getpass", mock_getpass):
                        iterator = iterator_factory()
                        # Mock _pelican_binary_exists to return True
                        iterator._pelican_binary_exists = MagicMock(return_value=True)

                        token = iterator._get_token_from_pelican_binary()

        # Verify password was requested and sent
        mock_getpass.assert_called_once_with(prompt="")
        assert mock_child.password_received == "test_password_123"

        # Verify token was extracted
        assert token == test_token

    def test_oidc_url_display_without_jupyter(self, mock_pexpect, iterator_factory, capsys):
        """Test that OIDC URLs are printed as text when not in Jupyter."""
        test_token = generate_test_token()
        oidc_url = "https://auth.example.com/device?code=ABC123"

        mock_child = MockPexpectChild(
            [
                (1, "", oidc_url),  # OIDC URL
                (2, f"\n{test_token}\n", ""),  # EOF with token
            ]
        )

        mock_pexpect.spawn = MagicMock(return_value=mock_child)

        with patch.dict(sys.modules, {"pexpect": mock_pexpect}):
            with patch("pelicanfs.token_content_iterator._PEXPECT_AVAILABLE", True):
                with patch("pelicanfs.token_content_iterator.pexpect", mock_pexpect):
                    iterator = iterator_factory()
                    iterator._pelican_binary_exists = MagicMock(return_value=True)

                    token = iterator._get_token_from_pelican_binary()

        captured = capsys.readouterr()
        assert f"Please visit: {oidc_url}" in captured.out
        assert token == test_token

    def test_timeout_handling(self, mock_pexpect, iterator_factory):
        """Test that timeouts are handled gracefully."""
        mock_child = MockPexpectChild(
            [
                (3, "Waiting for authentication...", ""),  # TIMEOUT
            ]
        )

        mock_pexpect.spawn = MagicMock(return_value=mock_child)

        with patch.dict(sys.modules, {"pexpect": mock_pexpect}):
            with patch("pelicanfs.token_content_iterator._PEXPECT_AVAILABLE", True):
                with patch("pelicanfs.token_content_iterator.pexpect", mock_pexpect):
                    iterator = iterator_factory(oidc_timeout_seconds=5)
                    iterator._pelican_binary_exists = MagicMock(return_value=True)

                    token = iterator._get_token_from_pelican_binary()

        assert token is None
        assert mock_child._closed

    def test_pelican_binary_not_found(self, iterator_factory, caplog):
        """Test behavior when pelican binary is not installed."""
        import logging

        iterator = iterator_factory()
        # Mock _pelican_binary_exists to return False
        iterator._pelican_binary_exists = MagicMock(return_value=False)

        # Start iteration to trigger OIDC_DEVICE_FLOW method
        iterator.method_index = iterator.get_method_index(TokenDiscoveryMethod.OIDC_DEVICE_FLOW)

        with caplog.at_level(logging.WARNING):
            with pytest.raises(StopIteration):
                next(iterator)

        assert "pelican" in caplog.text.lower()

    def test_non_zero_exit_status(self, mock_pexpect, iterator_factory):
        """Test handling of pelican binary returning non-zero exit status."""
        mock_child = MockPexpectChild(
            [
                (2, "Error: Authentication failed\n", ""),  # EOF with error
            ],
            exit_status=1,
        )

        mock_pexpect.spawn = MagicMock(return_value=mock_child)

        with patch.dict(sys.modules, {"pexpect": mock_pexpect}):
            with patch("pelicanfs.token_content_iterator._PEXPECT_AVAILABLE", True):
                with patch("pelicanfs.token_content_iterator.pexpect", mock_pexpect):
                    iterator = iterator_factory()
                    iterator._pelican_binary_exists = MagicMock(return_value=True)

                    token = iterator._get_token_from_pelican_binary()

        assert token is None

    def test_token_redaction_in_output(self, mock_pexpect, iterator_factory, capsys):
        """Test that tokens are redacted when displayed to user."""
        test_token = generate_test_token()

        # Simulate debug output containing token (without JSON braces so it gets printed)
        debug_output = f"Debug info without braces\nToken received: {test_token}\n"

        mock_child = MockPexpectChild(
            [
                (2, debug_output, ""),  # EOF with debug output containing token
            ]
        )

        mock_pexpect.spawn = MagicMock(return_value=mock_child)

        with patch.dict(sys.modules, {"pexpect": mock_pexpect}):
            with patch("pelicanfs.token_content_iterator._PEXPECT_AVAILABLE", True):
                with patch("pelicanfs.token_content_iterator.pexpect", mock_pexpect):
                    iterator = iterator_factory()
                    iterator._pelican_binary_exists = MagicMock(return_value=True)

                    token = iterator._get_token_from_pelican_binary()

        captured = capsys.readouterr()
        # Token should be redacted in displayed output
        assert "[TOKEN_REDACTED]" in captured.out
        # But the actual token should still be returned
        assert token == test_token


class TestJupyterURLDisplay:
    """Test the _display_url_for_user method specifically."""

    def test_display_url_for_user_oidc_url(self, iterator_factory, capsys):
        """Test that OIDC device flow URLs are displayed."""
        iterator = iterator_factory()

        oidc_url = "https://auth.example.com/device/activate?code=XYZ"
        iterator._display_url_for_user(oidc_url)

        captured = capsys.readouterr()
        assert f"Please visit: {oidc_url}" in captured.out

    def test_display_url_for_user_non_oidc_url(self, iterator_factory, capsys):
        """Test that non-OIDC URLs are not displayed as clickable."""
        iterator = iterator_factory()

        # A regular URL that's not an OIDC device flow URL
        regular_url = "https://example.com/some/path"
        iterator._display_url_for_user(regular_url)

        captured = capsys.readouterr()
        # Should NOT be displayed
        assert regular_url not in captured.out

    def test_display_url_redacts_tokens(self, iterator_factory, capsys):
        """Test that tokens embedded in URLs are redacted."""
        iterator = iterator_factory()

        test_token = generate_test_token()
        url_with_token = f"https://auth.example.com/device?token={test_token}"
        iterator._display_url_for_user(url_with_token)

        captured = capsys.readouterr()
        # Token should be redacted
        assert "[TOKEN_REDACTED]" in captured.out
        assert test_token not in captured.out

    def test_is_oidc_device_flow_url(self, iterator_factory):
        """Test URL pattern matching for OIDC device flow URLs."""
        iterator = iterator_factory()

        # Should match OIDC device flow URLs
        assert iterator._is_oidc_device_flow_url("https://auth.example.com/device")
        assert iterator._is_oidc_device_flow_url("https://auth.example.com/activate?code=123")
        assert iterator._is_oidc_device_flow_url("https://auth.example.com/oauth/authorize")
        assert iterator._is_oidc_device_flow_url("https://auth.example.com/login?redirect=x")

        # Should NOT match regular URLs
        assert not iterator._is_oidc_device_flow_url("https://example.com/")
        assert not iterator._is_oidc_device_flow_url("https://example.com/api/data")
        assert not iterator._is_oidc_device_flow_url("https://cache.example.com/namespace/file.txt")

    def test_jupyter_html_display_called(self, iterator_factory, capsys):
        """Test that IPython.display.HTML is called in Jupyter environment."""
        mock_display = MagicMock()
        mock_html_class = MagicMock(return_value="<html>")

        mock_ipython_display = MagicMock()
        mock_ipython_display.display = mock_display
        mock_ipython_display.HTML = mock_html_class

        iterator = iterator_factory()

        oidc_url = "https://auth.example.com/device?code=TEST"

        # Patch IPython.display to be available
        with patch.dict(sys.modules, {"IPython.display": mock_ipython_display}):
            iterator._display_url_for_user(oidc_url)

        # Verify HTML was constructed with clickable link
        mock_html_class.assert_called_once()
        html_call_arg = mock_html_class.call_args[0][0]
        assert "href=" in html_call_arg
        assert oidc_url in html_call_arg
        assert 'target="_blank"' in html_call_arg

        # Verify display was called
        mock_display.assert_called_once()

        # Also verify text fallback was printed
        captured = capsys.readouterr()
        assert f"Please visit: {oidc_url}" in captured.out


class TestPelicanBinaryFlags:
    """Test pelican binary command construction."""

    def test_get_pelican_flag_read_operation(self, iterator_factory):
        """Test flags for read operation."""
        iterator = iterator_factory(operation=TokenOperation.TokenRead)

        flags = iterator._get_pelican_flag()

        assert "-r" in flags
        assert "-w" not in flags

    def test_get_pelican_flag_write_operation(self, iterator_factory):
        """Test flags for write operation."""
        iterator = iterator_factory(operation=TokenOperation.TokenWrite)

        flags = iterator._get_pelican_flag()

        assert "-w" in flags
        assert "-r" not in flags

    def test_get_pelican_flag_default_operation(self, iterator_factory):
        """Test flags default to read when operation is None."""
        iterator = iterator_factory(operation=None)

        flags = iterator._get_pelican_flag()

        assert "-r" in flags
        assert "-w" not in flags


class TestPasswordPromptWithGetpass:
    """Test password prompting via getpass."""

    def test_getpass_called_on_password_prompt(self, mock_pexpect, iterator_factory):
        """Verify getpass.getpass is called when password prompt is detected."""
        test_token = generate_test_token()
        mock_child = MockPexpectChild(
            [
                (0, "", "Password: "),  # Password prompt
                (2, f"{test_token}\n", ""),  # EOF with token
            ]
        )

        mock_pexpect.spawn = MagicMock(return_value=mock_child)

        captured_password = []

        def mock_getpass(prompt):
            captured_password.append(prompt)
            return "secret123"

        with patch.dict(sys.modules, {"pexpect": mock_pexpect}):
            with patch("pelicanfs.token_content_iterator._PEXPECT_AVAILABLE", True):
                with patch("pelicanfs.token_content_iterator.pexpect", mock_pexpect):
                    with patch("getpass.getpass", mock_getpass):
                        iterator = iterator_factory()
                        iterator._pelican_binary_exists = MagicMock(return_value=True)
                        iterator._get_token_from_pelican_binary()

        # getpass should have been called with empty prompt
        assert captured_password == [""]
        # Password should have been sent to the child process
        assert mock_child.password_received == "secret123"

    def test_multiple_password_prompts(self, mock_pexpect, iterator_factory):
        """Test handling of multiple password prompts (e.g., retry)."""
        test_token = generate_test_token()
        mock_child = MockPexpectChild(
            [
                (0, "", "Password: "),  # First password prompt
                (0, "Invalid password\n", "Password: "),  # Second prompt after failure
                (2, f"Success\n{test_token}\n", ""),  # EOF with token
            ]
        )

        mock_pexpect.spawn = MagicMock(return_value=mock_child)

        password_calls = []

        def mock_getpass(prompt):
            password_calls.append(len(password_calls) + 1)
            return f"password{len(password_calls)}"

        with patch.dict(sys.modules, {"pexpect": mock_pexpect}):
            with patch("pelicanfs.token_content_iterator._PEXPECT_AVAILABLE", True):
                with patch("pelicanfs.token_content_iterator.pexpect", mock_pexpect):
                    with patch("getpass.getpass", mock_getpass):
                        iterator = iterator_factory()
                        iterator._pelican_binary_exists = MagicMock(return_value=True)
                        token = iterator._get_token_from_pelican_binary()

        # Should have prompted for password twice
        assert len(password_calls) == 2
        assert token == test_token


class TestPexpectNotAvailable:
    """Test behavior when pexpect is not installed."""

    def test_pexpect_not_available_warning(self, iterator_factory, caplog):
        """Test that appropriate warning is logged when pexpect is not available."""
        import logging

        with patch("pelicanfs.token_content_iterator._PEXPECT_AVAILABLE", False):
            iterator = iterator_factory()
            iterator._pelican_binary_exists = MagicMock(return_value=True)

            with caplog.at_level(logging.WARNING):
                token = iterator._get_token_from_pelican_binary()

        assert token is None
        assert "pexpect" in caplog.text.lower()
