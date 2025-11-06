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
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import List, Optional, Tuple
from urllib.parse import ParseResult, urlparse

from scitokens import SciToken

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
        dir_resp: object,
        operation: TokenOperation,
        token_name: Optional[str] = None,
        pelican_url: Optional[str] = None,
    ) -> None:
        self.DirResp: object = dir_resp
        self.DestinationURL: str = destination_url
        self.PelicanURL: Optional[str] = pelican_url
        self.TokenName: Optional[str] = token_name
        self.TokenLocation: Optional[str] = None
        self.Operation: TokenOperation = operation
        self.token: Optional[TokenInfo] = None
        self.Iterator: Optional[TokenContentIterator] = None
        self._lock: threading.Lock = threading.Lock()

    def set_token_location(self, token_location: str) -> None:
        """Sets the location (e.g., file path) where tokens can be found."""
        self.TokenLocation = token_location

    def set_token(self, contents: str) -> None:
        """Sets a custom token with a far future expiry (for testing or override)."""
        expiry: datetime = datetime.now(timezone.utc) + timedelta(days=365 * 100)
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
            if self.token and self.token.Expiry > datetime.now(timezone.utc) and self.token.Contents:
                return self.token.Contents

            potential_tokens: List[TokenInfo] = []
            operation = self.Operation

            try:
                parsed_url: ParseResult = urlparse(self.DestinationURL)
                object_path: str = parsed_url.path
                if not object_path:
                    raise InvalidDestinationURL("URL path is empty")
            except Exception as e:
                logger.error(f"Invalid DestinationURL: {self.DestinationURL} ({e})")
                raise InvalidDestinationURL(f"Invalid DestinationURL: {self.DestinationURL}") from e

            # Initialize iterator if not already set
            # The iterator will iterate and yield all potential tokens in the token location
            if self.Iterator is None:
                self.Iterator = TokenContentIterator(self.TokenLocation, self.TokenName, operation=self.Operation, destination_url=self.DestinationURL, pelican_url=self.PelicanURL)

            logger.debug("About to enter token validation loop")
            logger.debug(f"self.Iterator at validation loop: {self.Iterator}")
            try:
                # Use next() to get tokens one at a time from the iterator
                while True:
                    try:
                        contents = next(self.Iterator)
                        # Check if the token is valid and acceptable
                        logger.debug(f"Validating token for operation: {operation}")
                        valid, expiry = token_is_valid_and_acceptable(contents, object_path, self.DirResp, operation)
                        logger.debug(f"Token validation result: valid={valid}, expiry={expiry}")
                        if valid:
                            self.token = TokenInfo(contents, expiry)
                            return contents
                        elif contents and expiry > datetime.now(timezone.utc):
                            potential_tokens.append(TokenInfo(contents, expiry))
                    except StopIteration:
                        logger.debug("Token iterator reached StopIteration")
                        break
            except Exception as e:
                logger.error(f"Error iterating tokens: {e}")
                raise TokenIteratorException("Failed to fetch tokens due to iterator error") from e

            if potential_tokens:
                logger.warning("Using fallback token even though it may not be fully acceptable")
                self.token = potential_tokens[0]
                return potential_tokens[0].Contents

            logger.error("Credential is required, but currently missing")
            raise NoCredentialsException(f"Credential is required for {self.DestinationURL} but was not discovered")

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
    dir_resp: object,
    operation: TokenOperation,
) -> Tuple[bool, datetime]:
    """
    Validates a SciToken for expiration, issuer, namespace,
    and required scope based on the operation.

    Returns:
        Tuple (is_valid, expiry_datetime)
    """
    logger.debug(f"token_is_valid_and_acceptable called with operation: {operation}")

    try:
        # Try to deserialize the token without SSL verification
        # The SSL issue is likely happening when SciToken tries to fetch the public key
        token: SciToken = SciToken.deserialize(jwt_serialized)
        logger.debug("Successfully deserialized token")
    except (ValueError, Exception) as e:
        logger.debug(f"Failed to deserialize token: {jwt_serialized[:30]}... Error: {e}")
        return False, datetime.fromtimestamp(0, tz=timezone.utc)

    # Check if the token is expired
    exp = token.get("exp")
    if exp is None:
        logger.debug("Token missing exp claim")
        return False, datetime.fromtimestamp(0, tz=timezone.utc)

    expiry_dt: datetime = datetime.fromtimestamp(exp, tz=timezone.utc)
    logger.debug(f"Token expiry: {expiry_dt}")
    if expiry_dt <= datetime.now(timezone.utc):
        logger.debug(f"Token expired at {expiry_dt}")
        return False, expiry_dt

    # Get the allowed issuers from the director response and check if the token issuer is in the list
    issuers: List[str] = []
    if dir_resp and hasattr(dir_resp, "x_pel_tok_gen_hdr") and dir_resp.x_pel_tok_gen_hdr:
        issuers = dir_resp.x_pel_tok_gen_hdr.issuers or []
    logger.debug(f"Allowed issuers: {issuers}")
    logger.debug(f"Token issuer: {dict(token._verified_claims).get('iss')}")

    # Get the operation type and set the required scopes
    if operation in [TokenOperation.TokenWrite, TokenOperation.TokenSharedWrite]:
        ok_scopes = ["storage.modify", "storage.create"]
    elif operation in [TokenOperation.TokenRead, TokenOperation.TokenSharedRead]:
        ok_scopes = ["storage.read"]
    else:
        ok_scopes = []

    logger.debug(f"Required scopes for operation '{operation}': {ok_scopes}")
    logger.debug(f"Token scopes: {token.get('scope')}")

    token_scopes = token.get("scope", "")
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
    scope: Optional[str] = None,
    issuer: Optional[List[str]] = None,
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
        exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc)
        if exp_dt <= datetime.now(timezone.utc) + timedelta(seconds=timeleft):
            if warn:
                logger.warning(f"Token expired or about to expire at {exp_dt}")
            return False

    # Check if the token issuer is in the allowed list
    tok_issuer = dict(token._verified_claims).get("iss")
    if issuer and tok_issuer not in issuer:
        if warn:
            logger.warning(f"Token issuer {tok_issuer} not in allowed list: {issuer}")
        return False

    # Check if the token scope matches the required scope
    tok_scope = token.get("scope")
    if scope and (not tok_scope or scope not in tok_scope.split()):
        if warn:
            logger.warning(f"Token missing required scope: {scope} in {tok_scope}")
        return False

    return True
