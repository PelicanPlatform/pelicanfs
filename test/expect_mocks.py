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

Shared pexpect/wexpect mocks used by the OIDC device-flow tests
(test_tci.py and test_oidc_jupyter.py).
"""

from contextlib import contextmanager
from unittest.mock import patch


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


@contextmanager
def patch_expect_module(mock_module):
    """Patch the bound pexpect/wexpect module and exception constants."""
    with (
        patch("pelicanfs.token_content_iterator._expect_module", mock_module),
        patch("pelicanfs.token_content_iterator._EOF", MockEOF),
        patch("pelicanfs.token_content_iterator._TIMEOUT", MockTIMEOUT),
    ):
        yield
