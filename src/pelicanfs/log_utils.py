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
from typing import Optional, Union

# Finer than DEBUG for internal call-flow tracing (not enabled by default).
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def trace(logger: logging.Logger, msg: str, *args, **kwargs) -> None:
    if logger.isEnabledFor(TRACE):
        logger._log(TRACE, msg, args, **kwargs)


def format_token_for_log(token: Optional[Union[str, bytes]]) -> Optional[str]:
    """Return JWT header and payload (no signature) for safe, readable logging."""
    if not token:
        return None
    if isinstance(token, bytes):
        raw = token.decode("utf-8", errors="replace")
    else:
        raw = token
    raw = raw.removeprefix("Bearer ").strip()
    parts = raw.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return raw
