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

from typing import Protocol

__all__ = ["AuthHandler", "get_auth_handler", "set_auth_handler"]


class AuthHandler(Protocol):
    """
    The user interface for the OIDC device flow, for a program that owns the screen (such as a TUI).

    The methods are called from the thread that runs the flow, which is not the
    caller's thread. An exception from a method stops the flow.

    A handler can also have these optional methods:

    - ``finish(success: bool) -> None``: called once when the flow ends;
      ``success`` tells if a token was obtained.
    - ``cancelled() -> bool``: polled while pelican runs (not during
      ``ask_password``); True stops the flow.
    """

    def show(self, text: str, url: str | None = None) -> None:
        """Show one line of pelican output (no line ending). ``url`` is the device URL, if the line has one."""

    def ask_password(self, prompt: str) -> str:
        """Return the password for the local credential file. This blocks the flow."""
        ...


_handler: AuthHandler | None = None


def set_auth_handler(handler: AuthHandler | None) -> AuthHandler | None:
    """Set the handler for all filesystems in this process (None for the default). Returns the previous one."""
    global _handler
    previous, _handler = _handler, handler
    return previous


def get_auth_handler() -> AuthHandler | None:
    return _handler
