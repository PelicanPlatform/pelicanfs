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
import logging
import os
import platform
import re
import shutil
import traceback
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional

from igwn_auth_utils.scitokens import (
    _find_condor_creds_token_paths,
    default_bearer_token_file,
)

# Platform-specific imports for pexpect
_IS_WINDOWS = platform.system() == "Windows"

# Try to import pexpect (Unix) or wexpect (Windows)
_PEXPECT_AVAILABLE = False
_WEXPECT_AVAILABLE = False

if _IS_WINDOWS:
    try:
        import wexpect

        _WEXPECT_AVAILABLE = True
    except ImportError:
        pass
else:
    try:
        import pexpect

        _PEXPECT_AVAILABLE = True
    except ImportError:
        pass

logger = logging.getLogger("fsspec.pelican")

# Default constants for OIDC device flow (can be overridden)
DEFAULT_OIDC_TIMEOUT_SECONDS = 300  # 5 minutes


def get_token_from_file(token_location: str) -> str:
    logger.debug(f"Opening token file: {token_location}")
    try:
        with open(token_location, "r") as f:
            token_contents = f.read()
    except Exception as err:
        logger.error(f"Error reading from token file: {err}")
        raise

    token_str = token_contents.strip()

    # Check if the token is empty or whitespace only
    if not token_str:
        logger.warning(f"Token file {token_location} is empty or contains only whitespace")
        raise ValueError(f"Token file {token_location} is empty")

    if token_str.startswith("{"):
        try:
            token_parsed = json.loads(token_contents)
            access_key = token_parsed.get("access_token")
            if access_key:
                return access_key
            else:
                logger.debug("JSON token does not contain 'access_token' key, returning full token string")
                return token_str
        except json.JSONDecodeError as err:
            logger.debug(f"Unable to unmarshal file {token_location} as JSON (assuming it is a token instead): {err}")
            return token_str
    else:
        return token_str


class TokenDiscoveryMethod(Enum):
    LOCATION = auto()
    ENV_BEARER_TOKEN = auto()
    ENV_BEARER_TOKEN_FILE = auto()
    DEFAULT_BEARER_TOKEN = auto()
    ENV_TOKEN_PATH = auto()
    HTCONDOR_DISCOVERY = auto()
    HTCONDOR_FALLBACK = auto()
    OIDC_DEVICE_FLOW = auto()


@dataclass
class TokenContentIterator:
    """
    Iterator to locate and retrieve bearer tokens from multiple sources.

    The sources are checked in this order:
        1. Explicitly provided file path (via `location`)
        2. Environment variable BEARER_TOKEN
        3. Environment variable BEARER_TOKEN_FILE
        4. Default token file via default_bearer_token_file()
        5. Environment variable TOKEN (interpreted as file path)
        6. HTCondor discovery via _CONDOR_CREDS or .condor_creds directory
        7. OIDC device flow via pelican binary (final fallback)

    Attributes:
        location (str): Specific token file path (optional).
        name (str): Logical name of the token (used by HTCondor discovery).
        operation: Token operation type (read/write).
        destination_url (str): Destination URL for the token request.
        pelican_url (str): Pelican protocol URL (pelican://<federation-url>/<path>) for OIDC device flow.
        oidc_timeout_seconds (int): Timeout in seconds for OIDC device flow (default: 300).
        method_index (int): Internal index of the current discovery method.
        cred_locations (List[str]): Token file paths discovered via HTCondor fallback.
        fallback_index (int): Internal index of the current fallback cred_location
    """

    location: str
    name: str
    operation: Optional[object] = None
    destination_url: Optional[str] = None
    pelican_url: Optional[str] = None
    oidc_timeout_seconds: int = DEFAULT_OIDC_TIMEOUT_SECONDS
    method_index: int = 0
    cred_locations: List[str] = field(default_factory=list)
    fallback_index: int = 0

    def _pelican_binary_exists(self) -> bool:
        """Check if pelican binary exists in PATH"""
        logger.debug(f"Checking for pelican binary in PATH: {os.environ.get('PATH', '(not set)')}")
        result = shutil.which("pelican")
        return result is not None

    def _get_pelican_flag(self) -> list[str]:
        """
        Map token operation to pelican binary flags.

        Returns:
            list[str]: List of flags to pass to pelican binary
                      (-d for debug output based on log level,
                       -r for read, -w for write, -m for modify)
        """
        flags = []

        # If logger is set to DEBUG level, add -d flag for debug output
        if logger.isEnabledFor(logging.DEBUG):
            flags.append("-d")
            logger.debug("Adding -d flag to pelican binary for debug output")

        # Add operation flag
        if self.operation is None:
            flags.append("-r")  # default to read
        else:
            # Import TokenOperation here to avoid circular import
            from pelicanfs.token_generator import TokenOperation

            if self.operation in [TokenOperation.TokenRead, TokenOperation.TokenSharedRead]:
                flags.append("-r")
            elif self.operation in [TokenOperation.TokenWrite, TokenOperation.TokenSharedWrite]:
                flags.append("-w")
            else:
                flags.append("-r")  # default to read

        return flags

    def _is_oidc_device_flow_url(self, url: str) -> bool:
        """
        Check if a URL is an OIDC device flow authentication URL that users need to visit.

        OIDC device flow URLs typically contain paths like /device, /activate, or similar.
        URLs embedded in JSON debug output (containing braces, quotes) should not be treated
        as user-facing authentication URLs.

        Args:
            url: The URL to check

        Returns:
            bool: True if this appears to be an OIDC device flow URL for user authentication
        """
        # If the URL contains JSON-like characters, it's probably embedded in debug output
        if "{" in url or "}" in url or '"' in url or ':{"' in url:
            return False

        # OIDC device flow URLs typically have these path patterns
        oidc_patterns = ["/device", "/activate", "/oauth", "/authorize", "/login"]
        url_lower = url.lower()
        return any(pattern in url_lower for pattern in oidc_patterns)

    def _display_url_for_user(self, url: str) -> None:
        """
        Display a URL to the user, making it clickable in Jupyter environments.

        Only creates clickable links for actual OIDC device flow URLs that users
        need to visit. URLs embedded in debug JSON output are not made clickable.

        Args:
            url: The URL to potentially display as clickable
        """
        # Only create clickable links for actual OIDC device flow URLs
        if not self._is_oidc_device_flow_url(url):
            # This is likely a URL embedded in debug output - just print it normally
            # without creating a clickable link
            return

        # Filter out tokens
        jwt_pattern = r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
        filtered_url = re.sub(jwt_pattern, "[TOKEN_REDACTED]", url)

        # Try to make URLs clickable in Jupyter/IPython
        try:
            from IPython.display import HTML, display

            display(HTML(f'<a href="{filtered_url}" target="_blank">Click here to authenticate: {filtered_url}</a>'))
        except ImportError:
            pass

        # Always print the text version
        print(f"Please visit: {filtered_url}")

    def _get_token_from_pelican_binary(self) -> Optional[str]:
        """
        Invoke pelican binary to get token via OIDC device flow.

        Uses pexpect (Unix) or wexpect (Windows) to interact with the pelican
        binary in a pseudo-terminal. This allows proper handling of password
        prompts and interactive OIDC device flow regardless of the environment
        (terminal, Jupyter, IDE, etc.).

        Returns:
            str: JWT token if successful, None otherwise
        """
        if not self.pelican_url:
            logger.warning("Cannot invoke pelican binary without pelican URL")
            return None

        # Check if pexpect/wexpect is available
        if _IS_WINDOWS:
            if not _WEXPECT_AVAILABLE:
                logger.warning("wexpect is required for OIDC device flow on Windows. " "Install it with: pip install wexpect")
                return None
        else:
            if not _PEXPECT_AVAILABLE:
                logger.warning("pexpect is required for OIDC device flow. " "Install it with: pip install pexpect")
                return None

        flags = self._get_pelican_flag()
        cmd_str = f"pelican token fetch {self.pelican_url} {' '.join(flags)}"

        logger.info(f"Invoking OIDC device flow via pelican binary: {cmd_str}")

        try:
            # Use pexpect/wexpect to spawn the process with a PTY
            if _IS_WINDOWS:
                child = wexpect.spawn(cmd_str, timeout=self.oidc_timeout_seconds)
            else:
                child = pexpect.spawn(cmd_str, timeout=self.oidc_timeout_seconds, encoding="utf-8")

            output_lines = []
            jwt_pattern = r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"

            # Patterns to expect from pelican binary
            # The order matters - check for password prompt, OIDC URLs, EOF, and timeout
            # Use a more specific pattern for OIDC device flow URLs to avoid matching
            # URLs in debug JSON output
            patterns = [
                r"[Pp]assword[:\s]*",  # Password prompt (case-insensitive)
                r'https?://[^\s"}\]]+/(?:device|activate|oauth|authorize|login)[^\s"}\]]*',  # OIDC device flow URL
                pexpect.EOF if not _IS_WINDOWS else wexpect.EOF,
                pexpect.TIMEOUT if not _IS_WINDOWS else wexpect.TIMEOUT,
            ]

            while True:
                try:
                    index = child.expect(patterns)

                    # Capture any output before the match
                    if child.before:
                        before_text = child.before if isinstance(child.before, str) else child.before.decode("utf-8", errors="replace")
                        output_lines.append(before_text)
                        # Only display non-JSON debug output to the user
                        # JSON debug output (containing braces) is logged but not printed
                        filtered = re.sub(jwt_pattern, "[TOKEN_REDACTED]", before_text)
                        if filtered.strip() and "{" not in filtered and "}" not in filtered:
                            print(filtered, end="")

                    if index == 0:  # Password prompt
                        # Get the matched text
                        match_text = child.after if isinstance(child.after, str) else child.after.decode("utf-8", errors="replace")
                        output_lines.append(match_text)
                        print(match_text, end="")

                        # Use getpass to securely prompt for password
                        import getpass

                        password = getpass.getpass("")
                        child.sendline(password)

                    elif index == 1:  # URL (device flow)
                        # Get the URL
                        url = child.after if isinstance(child.after, str) else child.after.decode("utf-8", errors="replace")
                        output_lines.append(url)
                        self._display_url_for_user(url)

                    elif index == 2:  # EOF - process finished
                        # Capture any remaining output
                        if child.before:
                            before_text = child.before if isinstance(child.before, str) else child.before.decode("utf-8", errors="replace")
                            output_lines.append(before_text)
                            # Only display non-JSON debug output
                            filtered = re.sub(jwt_pattern, "[TOKEN_REDACTED]", before_text)
                            if filtered.strip() and "{" not in filtered and "}" not in filtered:
                                print(filtered, end="")
                        break

                    elif index == 3:  # Timeout
                        logger.warning(f"Pelican binary timed out (exceeded {self.oidc_timeout_seconds} seconds)")
                        child.close(force=True)
                        return None

                except (pexpect.EOF if not _IS_WINDOWS else wexpect.EOF):
                    break
                except (pexpect.TIMEOUT if not _IS_WINDOWS else wexpect.TIMEOUT):
                    logger.warning(f"Pelican binary timed out (exceeded {self.oidc_timeout_seconds} seconds)")
                    child.close(force=True)
                    return None

            child.close()

            # Check exit status
            if child.exitstatus != 0:
                logger.debug(f"Pelican binary exited with code {child.exitstatus}")
                return None

            # Extract JWT token from captured output
            full_output = "".join(output_lines)
            matches = re.findall(jwt_pattern, full_output)

            if matches:
                token = matches[-1]
                logger.info("Successfully acquired token via OIDC device flow")
                return token
            else:
                logger.warning("Could not extract JWT token from pelican binary output")
                logger.debug(f"Output was: {re.sub(jwt_pattern, '[TOKEN_REDACTED]', full_output)}")
                return None

        except Exception as err:
            logger.debug(f"Error invoking pelican binary: {err}")
            logger.debug(traceback.format_exc())
            return None

    def get_method_index(self, method: TokenDiscoveryMethod) -> int:
        """
        Get the index of a specific token discovery method.

        This method provides a stable way for tests to reference specific discovery methods
        without relying on their position in the enum, making tests less fragile when
        new discovery methods are added.

        Args:
            method: The TokenDiscoveryMethod to find

        Returns:
            int: The index of the method in the methods list

        Raises:
            ValueError: If the method is not in the methods list
        """
        return self.methods.index(method)

    def __post_init__(self):
        self.methods = list(TokenDiscoveryMethod)
        # Ensure HTCONDOR_FALLBACK is always available after HTCONDOR_DISCOVERY
        if TokenDiscoveryMethod.HTCONDOR_DISCOVERY in self.methods and TokenDiscoveryMethod.HTCONDOR_FALLBACK not in self.methods:
            # Find the index of HTCONDOR_DISCOVERY and insert HTCONDOR_FALLBACK after it
            discovery_index = self.methods.index(TokenDiscoveryMethod.HTCONDOR_DISCOVERY)
            self.methods.insert(discovery_index + 1, TokenDiscoveryMethod.HTCONDOR_FALLBACK)

    def __iter__(self):
        return self

    def __next__(self) -> str:
        while self.method_index < len(self.methods):
            method = self.methods[self.method_index]
            self.method_index += 1
            logger.debug(f"Trying token discovery method: {method}")

            match method:
                case TokenDiscoveryMethod.LOCATION:
                    if self.location:
                        logger.debug(f"Using API-specified token location: {self.location}")
                        try:
                            if os.path.exists(self.location) and os.access(self.location, os.R_OK):
                                return get_token_from_file(self.location)
                            else:
                                raise OSError(f"File {self.location} is not readable")
                        except Exception as err:
                            logger.warning(f"Token file at {self.location} is not readable: {err}")

                case TokenDiscoveryMethod.ENV_BEARER_TOKEN:
                    token = os.getenv("BEARER_TOKEN")
                    if token:
                        logger.debug("Using token from BEARER_TOKEN env var")
                        return token

                case TokenDiscoveryMethod.ENV_BEARER_TOKEN_FILE:
                    token_file = os.getenv("BEARER_TOKEN_FILE")
                    if token_file:
                        logger.debug("Using token from BEARER_TOKEN_FILE env var")
                        try:
                            if os.path.exists(token_file) and os.access(token_file, os.R_OK):
                                return get_token_from_file(token_file)
                            else:
                                raise OSError(f"File {token_file} is not readable")
                        except Exception as err:
                            logger.warning(f"Could not read BEARER_TOKEN_FILE: {err}")

                case TokenDiscoveryMethod.DEFAULT_BEARER_TOKEN:
                    token_file = default_bearer_token_file()
                    if os.path.exists(token_file):
                        logger.debug(f"Using token from default bearer token file: {token_file}")
                        try:
                            token = get_token_from_file(token_file)
                            logger.debug(f"Successfully read token from default file: {token[:30] if token else 'None'}...")
                            return token
                        except Exception as err:
                            logger.warning(f"Could not read default bearer token: {err}")

                case TokenDiscoveryMethod.ENV_TOKEN_PATH:
                    token_path = os.getenv("TOKEN")
                    if token_path:
                        if not os.path.exists(token_path):
                            logger.warning(f"Environment variable TOKEN is set, but file does not exist: {token_path}")
                        else:
                            try:
                                logger.debug("Using token from TOKEN environment variable")
                                return get_token_from_file(token_path)
                            except Exception as err:
                                logger.warning(f"Error reading token from {token_path}: {err}")

                case TokenDiscoveryMethod.HTCONDOR_DISCOVERY:
                    self.cred_locations = self.discoverHTCondorTokenLocations(self.name)
                    # HTCONDOR_FALLBACK will be handled in the next iteration

                case TokenDiscoveryMethod.HTCONDOR_FALLBACK:
                    if self.cred_locations:  # Only try fallback if we have locations
                        while self.fallback_index < len(self.cred_locations):
                            token_path = self.cred_locations[self.fallback_index]
                            self.fallback_index += 1
                            try:
                                return get_token_from_file(token_path)
                            except Exception as err:
                                logger.warning(f"Failed to read fallback token at {token_path}: {err}")
                    else:
                        logger.debug("No cred_locations found for HTCONDOR_FALLBACK")
                    # No fallback tokens left to try

                case TokenDiscoveryMethod.OIDC_DEVICE_FLOW:
                    if not self._pelican_binary_exists():
                        logger.warning(
                            "OAuth token generation is only available when the 'pelican' binary is installed and available in PATH. "
                            "To install the pelican binary, please visit: https://docs.pelicanplatform.org/install"
                        )
                        continue

                    token = self._get_token_from_pelican_binary()
                    if token:
                        return token

        logger.debug("No more token sources to try")
        raise StopIteration

    def discoverHTCondorTokenLocations(self, tokenName: str) -> List[str]:
        """
        Discover possible HTCondor token file locations based on a logical token name.

        Supports environment variable _CONDOR_CREDS or defaults to `.condor_creds` in the
        current directory. If the token name includes dots, will try replacing them with
        underscores as HTCondor may sanitize filenames that way.

        Args:
            tokenName (str): Logical name of the token.

        Returns:
            List[str]: List of possible token file paths to try.
        """
        tokenLocations = []

        # Handle dot replacement recursively
        if tokenName and "." in tokenName:
            underscoreTokenName = tokenName.replace(".", "_")
            tokenLocations = self.discoverHTCondorTokenLocations(underscoreTokenName)
            if tokenLocations:
                return tokenLocations

        credsDir = os.getenv("_CONDOR_CREDS", ".condor_creds")

        if tokenName:
            tokenPath = os.path.join(credsDir, tokenName)
            tokenUsePath = os.path.join(credsDir, f"{tokenName}.use")
            if not os.path.exists(tokenPath):
                logger.warning(f"Environment variable _CONDOR_CREDS is set, but the credential file is not readable: {tokenPath}")
            else:
                tokenLocations.append(tokenUsePath)
                return tokenLocations
        else:
            scitokensUsePath = os.path.join(credsDir, "scitokens.use")
            if os.path.exists(scitokensUsePath):
                tokenLocations.append(scitokensUsePath)

        # Use _find_condor_creds_token_paths() generator to find *.use files
        try:
            condor_paths = _find_condor_creds_token_paths()
            if condor_paths is not None:
                for token_path in condor_paths:
                    baseName = os.path.basename(str(token_path))
                    # Skip special files
                    if baseName == "scitokens.use" or baseName.startswith("."):
                        continue
                    tokenLocations.append(str(token_path))
        except Exception as err:
            logger.warning(f"Failure when iterating through directory to look through tokens: {err}")

        return tokenLocations
