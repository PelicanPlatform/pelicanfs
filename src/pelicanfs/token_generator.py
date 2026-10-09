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

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from urllib.parse import ParseResult, urlparse

from scitokens import SciToken

from pelicanfs.dir_header_parser import DirectorResponse
from pelicanfs.exceptions import (
    InvalidDestinationURL,
    NoCredentialsException,
    TokenIteratorException,
)
from pelicanfs.token_content_iterator import TokenContentIterator

logger = logging.getLogger("fsspec.pelican")


@dataclass
class TokenInfo:
    """Token information including contents and expiration time"""

    Contents: str
    Expiry: datetime


class TokenOperation(Enum):
    """
    Enumeration of token operation types.

    - TokenRead: Read-only access
    - TokenWrite: Read/write access
    - TokenSharedRead: Read-only access with shared token
    - TokenSharedWrite: Read/write access with shared token
    """

    TokenRead = auto()
    TokenWrite = auto()
    TokenSharedRead = auto()
    TokenSharedWrite = auto()


class TokenGenerator:
    """
    Responsible for managing and retrieving valid tokens based on operation,
    destination URL, and token location.
    """

    def __init__(
        self,
        destination_url: str,
        dir_resp: DirectorResponse | None,
        operation: TokenOperation,
        token_name: str | None = None,
        pelican_url: str | None = None,
        oidc_timeout_seconds: int = 300,
        pty_buffer_size: int = 1024,
        select_timeout: float = 0.1,
    ) -> None:
        self.DirResp: DirectorResponse | None = dir_resp
        self.DestinationURL: str = destination_url
        self.PelicanURL: str | None = pelican_url
        self.TokenName: str | None = token_name
        self.TokenLocation: str | None = None
        self.Operation: TokenOperation = operation
        self.token: TokenInfo | None = None
        self.Iterator: TokenContentIterator | None = None
        self._lock: threading.Lock = threading.Lock()
        # OIDC device flow configuration
        self.oidc_timeout_seconds: int = oidc_timeout_seconds
        self.pty_buffer_size: int = pty_buffer_size
        self.select_timeout: float = select_timeout

    def set_token_location(self, token_location: str) -> None:
        """Sets the location (e.g., file path) where tokens can be found."""
        self.TokenLocation = token_location

    def set_token(self, contents: str) -> None:
        """Sets a custom token with a far future expiry (for testing or override)."""
        expiry: datetime = datetime.now(UTC) + timedelta(days=365 * 100)
        self.token = TokenInfo(contents, expiry)

    def set_token_name(self, name: str) -> None:
        """Sets the token name used to identify which token to use."""
        self.TokenName = name

    def copy(self) -> "TokenGenerator":
        """Creates a shallow copy of the token generator with shared destination and operation."""
        new_copy = TokenGenerator(self.DestinationURL, self.DirResp, self.Operation)
        new_copy.TokenName = self.TokenName
        return new_copy

    def get_token(self) -> str:
        """
        Retrieves a valid token either from cache or by iterating available tokens.
        Ensures token is valid for the given operation and destination.
        """
        # This needs to be thread safe
        with self._lock:
            if self.token and self.token.Expiry > datetime.now(UTC) and self.token.Contents:
                return self.token.Contents

            potential_tokens: list[TokenInfo] = []
            operation = self.Operation

            try:
                parsed_url: ParseResult = urlparse(self.DestinationURL)
                object_path: str = parsed_url.path
                if not object_path:
                    msg = "URL path is empty"
                    raise InvalidDestinationURL(msg)
            except Exception as e:
                logger.error("Invalid DestinationURL: %s (%s)", self.DestinationURL, e)
                msg = f"Invalid DestinationURL: {self.DestinationURL}"
                raise InvalidDestinationURL(msg) from e

            # Initialize iterator if not already set
            # The iterator will iterate and yield all potential tokens in the token location
            if self.Iterator is None:
                self.Iterator = TokenContentIterator(
                    self.TokenLocation,
                    self.TokenName,
                    operation=self.Operation,
                    destination_url=self.DestinationURL,
                    pelican_url=self.PelicanURL,
                    oidc_timeout_seconds=self.oidc_timeout_seconds,
                    pty_buffer_size=self.pty_buffer_size,
                    select_timeout=self.select_timeout,
                )

            logger.debug("About to enter token validation loop")
            logger.debug("self.Iterator at validation loop: %s", self.Iterator)
            try:
                # Use next() to get tokens one at a time from the iterator
                while True:
                    try:
                        contents = next(self.Iterator)
                        # Check if the token is valid and acceptable
                        logger.debug("Validating token for operation: %s", operation)
                        valid, expiry = token_is_valid_and_acceptable(contents, object_path, self.DirResp, operation)
                        logger.debug("Token validation result: valid=%s, expiry=%s", valid, expiry)
                        if valid:
                            self.token = TokenInfo(contents, expiry)
                            return contents
                        if contents and expiry > datetime.now(UTC):
                            potential_tokens.append(TokenInfo(contents, expiry))
                    except StopIteration:
                        logger.debug("Token iterator reached StopIteration")
                        break
            except Exception as e:
                logger.error("Error iterating tokens: %s", e)
                msg = "Failed to fetch tokens due to iterator error"
                raise TokenIteratorException(msg) from e

            if potential_tokens:
                logger.warning("Using fallback token even though it may not be fully acceptable")
                self.token = potential_tokens[0]
                return potential_tokens[0].Contents

            logger.error("Credential is required, but currently missing")
            msg = f"Credential is required for {self.DestinationURL} but was not discovered"
            raise NoCredentialsException(msg)

    def get(self) -> str:
        """Alias for get_token()."""
        return self.get_token()


def _is_path_prefix(object_name: str, resource: str) -> bool:
    """
    Check if object_name is a proper path prefix of resource.

    This ensures that resource is a proper directory prefix, not just a string prefix.
    Examples:
    - _is_path_prefix("/foo/bar/file.txt", "/foo/bar") -> True
    - _is_path_prefix("/foo/barz/file.txt", "/foo/bar") -> False
    - _is_path_prefix("/foo/bar", "/foo/bar") -> True
    """
    if not object_name.startswith(resource):
        return False

    # If object_name is exactly the resource, it's a match
    if object_name == resource:
        return True

    # For proper path prefix, the next character after the resource must be '/'
    # This ensures we're matching directory boundaries, not just string prefixes
    if len(object_name) > len(resource):
        return object_name[len(resource)] == "/"

    return False


def token_is_valid_and_acceptable(
    jwt_serialized: str,
    object_name: str,
    dir_resp: DirectorResponse | None,
    operation: TokenOperation,
) -> tuple[bool, datetime]:
    """
    Validates a SciToken for expiration, issuer, namespace,
    and required scope based on the operation.

    Returns:
        Tuple (is_valid, expiry_datetime)
    """
    logger.debug("token_is_valid_and_acceptable called with operation: %s", operation)

    try:
        # Try to deserialize the token without SSL verification
        # The SSL issue is likely happening when SciToken tries to fetch the public key
        token: SciToken = SciToken.deserialize(jwt_serialized)
        logger.debug("Successfully deserialized token")
    except Exception as e:
        logger.debug("Failed to deserialize token: %s... Error: %s", jwt_serialized[:30], e)
        return False, datetime.fromtimestamp(0, tz=UTC)

    # Check if the token is expired
    exp = token.get("exp")
    if exp is None:
        logger.debug("Token missing exp claim")
        return False, datetime.fromtimestamp(0, tz=UTC)

    expiry_dt: datetime = datetime.fromtimestamp(exp, tz=UTC)
    logger.debug("Token expiry: %s", expiry_dt)
    if expiry_dt <= datetime.now(UTC):
        logger.debug("Token expired at %s", expiry_dt)
        return False, expiry_dt

    # Get the allowed issuers from the director response and check if the token issuer is in the list
    issuers: list[str] = []
    if dir_resp and hasattr(dir_resp, "x_pel_tok_gen_hdr") and dir_resp.x_pel_tok_gen_hdr:
        issuers = dir_resp.x_pel_tok_gen_hdr.issuers or []
    logger.debug("Allowed issuers: %s", issuers)
    logger.debug("Token issuer: %s", dict(token._verified_claims).get("iss"))

    # Get the operation type and set the required scopes
    if operation in [TokenOperation.TokenWrite, TokenOperation.TokenSharedWrite]:
        ok_scopes = ["storage.modify", "storage.create"]
    elif operation in [TokenOperation.TokenRead, TokenOperation.TokenSharedRead]:
        ok_scopes = ["storage.read"]
    else:
        ok_scopes = []

    logger.debug("Required scopes for operation '%s': %s", operation, ok_scopes)
    logger.debug("Token scopes: %s", token.get("scope"))

    token_scopes = token.get("scope") or ""
    scope_list = token_scopes.split()
    acceptable_scope = False

    for scope in scope_list:
        scope_parts = scope.split(":", 1)
        permission = scope_parts[0]
        resource = scope_parts[1] if len(scope_parts) == 2 else None

        if permission not in ok_scopes:
            continue

        # Validate standard claims (scope, issuer, expiry, etc.)
        if not is_valid_token(
            token=token,
            scope=scope,
            issuer=issuers,
            timeleft=0,
            warn=False,
        ):
            continue

        if not resource:
            acceptable_scope = True
            break

        is_shared = operation in [TokenOperation.TokenSharedRead, TokenOperation.TokenSharedWrite]

        if (is_shared and object_name == resource) or _is_path_prefix(object_name, resource):
            acceptable_scope = True
            break

    if not acceptable_scope:
        logger.debug("No acceptable scope found in token")
        return False, expiry_dt

    return True, expiry_dt


def is_valid_token(
    token: SciToken,
    scope: str | None = None,
    issuer: list[str] | None = None,
    timeleft: int = 0,
    warn: bool = True,
) -> bool:
    """
    Helper to check token claims against expected audience, scope, issuer, and expiry.

    Returns:
        True if all checks pass, otherwise False.
    """
    if issuer is None:
        issuer = []

    # Check if the token is expired
    exp = token.get("exp")
    if exp:
        exp_dt = datetime.fromtimestamp(exp, tz=UTC)
        if exp_dt <= datetime.now(UTC) + timedelta(seconds=timeleft):
            if warn:
                logger.warning("Token expired or about to expire at %s", exp_dt)
            return False

    # Check if the token issuer is in the allowed list
    tok_issuer = dict(token._verified_claims).get("iss")
    if issuer and tok_issuer not in issuer:
        if warn:
            logger.warning("Token issuer %s not in allowed list: %s", tok_issuer, issuer)
        return False

    # Check if the token scope matches the required scope
    tok_scope = token.get("scope")
    if scope and (not tok_scope or scope not in tok_scope.split()):
        if warn:
            logger.warning("Token missing required scope: %s in %s", scope, tok_scope)
        return False

    return True
