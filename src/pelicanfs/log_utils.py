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

# Finer than DEBUG for internal call-flow tracing (not enabled by default).
TRACE = 5


def trace(logger: logging.Logger, msg: str, *args, **kwargs) -> None:
    logger.log(TRACE, msg, *args, stacklevel=2, **kwargs)


class TokenForLog:
    """Show a JWT as header and payload (no signature), and hide any other value.

    Formatting runs only when a log record is emitted.
    """

    def __init__(self, token: str | None) -> None:
        self.token = token

    def __str__(self) -> str:
        if not self.token:
            return "None"
        raw = self.token.removeprefix("Bearer ").strip()
        parts = raw.split(".")
        # A JWT header is base64url JSON, so it starts with the encoding of '{"'.
        if len(parts) == 3 and raw.startswith("eyJ"):
            return f"{parts[0]}.{parts[1]}"
        return f"<non-jwt len={len(raw)}>"
