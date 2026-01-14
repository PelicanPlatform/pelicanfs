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
import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional

from igwn_auth_utils.scitokens import (
    _find_condor_creds_token_paths,
    default_bearer_token_file,
)

# Platform-specific imports
_IS_WINDOWS = platform.system() == "Windows"
if not _IS_WINDOWS:
    import pty
    import select

logger = logging.getLogger("fsspec.pelican")

# Default constants for OIDC device flow (can be overridden)
DEFAULT_OIDC_TIMEOUT_SECONDS = 300  # 5 minutes
DEFAULT_PTY_BUFFER_SIZE = 1024
DEFAULT_SELECT_TIMEOUT = 0.1  # 100ms for responsive I/O


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
        pty_buffer_size (int): Buffer size for PTY I/O (default: 1024).
        select_timeout (float): Timeout in seconds for select() calls (default: 0.1).
        method_index (int): Internal index of the current discovery method.
        cred_locations (List[str]): Token file paths discovered via HTCondor fallback.
        index (int): Internal index of the current fallback cred_location
    """

    location: str
    name: str
    operation: Optional[object] = None
    destination_url: Optional[str] = None
    pelican_url: Optional[str] = None
    oidc_timeout_seconds: int = DEFAULT_OIDC_TIMEOUT_SECONDS
    pty_buffer_size: int = DEFAULT_PTY_BUFFER_SIZE
    select_timeout: float = DEFAULT_SELECT_TIMEOUT
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

    def _get_token_from_pelican_binary(self) -> Optional[str]:
        """
        Invoke pelican binary to get token via OIDC device flow.

        Uses platform-specific approach for secure password input:
        - Unix: pty module with stdin from /dev/tty
        - Windows: subprocess with inherited stdin

        Returns:
            str: JWT token if successful, None otherwise
        """

        if not self.pelican_url:
            logger.warning("Cannot invoke pelican binary without pelican URL")
            return None

        flags = self._get_pelican_flag()
        cmd = ["pelican"] + ["token", "fetch", self.pelican_url] + flags

        logger.info(f"Invoking OIDC device flow via pelican binary: {' '.join(cmd)}")

        try:
            # Run the pelican binary with a PTY (pseudo-terminal) to allow interactive OIDC device flow
            # This lets the user see prompts and interact while we capture the output

            output_data = []
            start_time = time.time()

            # Platform-specific PTY setup
            if _IS_WINDOWS:
                # Windows: Let the process inherit stdin from the console for password prompts
                # We use PIPE for stdout/stderr to capture output, but inherit stdin
                # This allows the pelican binary to handle password prompts directly
                process = subprocess.Popen(
                    cmd,
                    stdin=None,  # Inherit stdin from parent process
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # Merge stderr into stdout
                    text=False,
                    bufsize=0,  # Unbuffered for real-time output
                )

                def read_from_pty():
                    """Read available data from subprocess stdout"""
                    try:
                        # Non-blocking read with timeout
                        import msvcrt

                        if msvcrt.kbhit() or process.stdout:
                            return process.stdout.read(self.pty_buffer_size)
                        return b""
                    except Exception:
                        return b""

                def write_to_pty(data):
                    """Not used on Windows with inherited stdin"""
                    pass

                def is_alive():
                    """Check if process is still running"""
                    return process.poll() is None

                stdin_data_to_send = None
            else:
                # Unix: use pty module
                import termios

                master_fd, slave_fd = pty.openpty()

                # Try to open /dev/tty for stdin so password prompts work correctly
                # If /dev/tty is not available, fall back to slave_fd
                try:
                    tty_fd = os.open("/dev/tty", os.O_RDWR)
                    process = subprocess.Popen(cmd, stdin=tty_fd, stdout=slave_fd, stderr=slave_fd, text=False)
                    os.close(tty_fd)
                except (OSError, FileNotFoundError):
                    # /dev/tty not available (e.g., Jupyter with redirected stdin)
                    # Disable echo on both master and slave PTY to prevent password echo
                    # This only affects the fallback case where stdin goes through the PTY
                    try:
                        # Disable echo on slave side
                        attrs = termios.tcgetattr(slave_fd)
                        attrs[3] = attrs[3] & ~termios.ECHO  # Disable ECHO flag
                        termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)

                        # Also disable echo on master side
                        attrs = termios.tcgetattr(master_fd)
                        attrs[3] = attrs[3] & ~termios.ECHO  # Disable ECHO flag
                        termios.tcsetattr(master_fd, termios.TCSANOW, attrs)
                    except Exception as e:
                        logger.debug(f"Could not disable echo on PTY: {e}")

                    process = subprocess.Popen(cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, text=False)
                os.close(slave_fd)

                def read_from_pty():
                    """Read available data from Unix PTY"""
                    ready, _, _ = select.select([master_fd], [], [], self.select_timeout)
                    if ready:
                        try:
                            return os.read(master_fd, self.pty_buffer_size)
                        except OSError:
                            return b""
                    return b""

                def write_to_pty(data):
                    """Write data to Unix PTY"""
                    os.write(master_fd, data)

                def is_alive():
                    """Check if process is still running"""
                    return process.poll() is None

                # Check if stdin has been redirected (e.g., StringIO in Jupyter)
                # If /dev/tty is available, the process reads from the terminal directly
                # If not, we need to forward any redirected stdin data
                stdin_data_to_send = None
                try:
                    stdin_fd = sys.stdin.fileno()
                    # Check if stdin is redirected (not a terminal)
                    if not os.isatty(stdin_fd):
                        # Stdin is redirected, read the data to forward it
                        try:
                            stdin_data_to_send = sys.stdin.read()
                            if stdin_data_to_send:
                                logger.debug(f"Read {len(stdin_data_to_send)} chars from redirected stdin")
                        except Exception as e:
                            logger.debug(f"Could not read from redirected stdin: {e}")
                            stdin_data_to_send = None
                except (AttributeError, io.UnsupportedOperation):
                    # stdin doesn't have fileno() - likely redirected (e.g., Jupyter)
                    try:
                        stdin_data_to_send = sys.stdin.read()
                        if stdin_data_to_send:
                            logger.debug(f"Read {len(stdin_data_to_send)} chars from redirected stdin")
                    except Exception as e:
                        logger.debug(f"Could not read from redirected stdin: {e}")
                        stdin_data_to_send = None

            def read_and_echo_output():
                """Helper to read from PTY, echo to terminal (filtering sensitive data), and store data"""
                data = read_from_pty()
                if data:
                    # Store raw data for token extraction
                    output_data.append(data)

                    # Filter sensitive information before echoing to terminal
                    decoded_data = data.decode("utf-8", errors="replace")

                    # Hide passwords and tokens using regex patterns
                    # Pattern for JWT tokens
                    jwt_pattern = r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
                    filtered_data = re.sub(jwt_pattern, "[TOKEN_REDACTED]", decoded_data)

                    # Try to write to stdout.buffer (for real terminals)
                    # Fall back to stdout.write() for Jupyter/IPython environments
                    try:
                        sys.stdout.buffer.write(filtered_data.encode("utf-8"))
                        sys.stdout.buffer.flush()
                    except AttributeError:
                        # stdout doesn't have buffer (e.g., Jupyter/IPython)
                        sys.stdout.write(filtered_data)
                        sys.stdout.flush()
                return data

            # Read from PTY and echo to terminal, also forward stdin to the process
            try:
                while True:
                    # Check timeout
                    if time.time() - start_time > self.oidc_timeout_seconds:
                        process.kill()
                        logger.warning(f"Pelican binary timed out (exceeded {self.oidc_timeout_seconds} seconds)")
                        return None

                    # Check if process is still running
                    if not is_alive():
                        # Process finished, read any remaining output
                        while True:
                            data = read_from_pty()
                            if not data:
                                break
                        break

                    # Read subprocess output
                    read_and_echo_output()

                    # Handle stdin forwarding (Unix only - Windows uses simpler approach)
                    # Note: When /dev/tty is available, pelican reads stdin directly from the terminal
                    # This code only applies when stdin is redirected (e.g., Jupyter notebooks)
                    if not _IS_WINDOWS:
                        if stdin_data_to_send:
                            # Redirected stdin - send buffered data once
                            try:
                                write_to_pty(stdin_data_to_send.encode("utf-8"))
                                stdin_data_to_send = None  # Only send once
                            except OSError as e:
                                logger.debug(f"Error writing redirected stdin to PTY: {e}")
            finally:
                # Cleanup platform-specific resources
                if not _IS_WINDOWS:
                    # Unix cleanup
                    os.close(master_fd)

            # Wait for process to finish and get return code
            process.wait()
            returncode = process.returncode

            if returncode != 0:
                logger.debug(f"Pelican binary exited with code {returncode}")
                return None

            # Extract JWT token from captured output
            full_output = b"".join(output_data).decode("utf-8", errors="replace")
            jwt_pattern = r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
            matches = re.findall(jwt_pattern, full_output)

            if matches:
                token = matches[-1]
                logger.info("Successfully acquired token via OIDC device flow")
                return token
            else:
                logger.warning("Could not extract JWT token from pelican binary output")
                logger.debug(f"Output was: {full_output}")
                return None

        except Exception as err:
            logger.debug(f"Error invoking pelican binary: {err}")
            import traceback

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
