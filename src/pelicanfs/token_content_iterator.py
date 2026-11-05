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

import getpass
import html
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
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Self

from igwn_auth_utils.scitokens import (
    _find_condor_creds_token_paths,
    default_bearer_token_file,
)

# Platform-specific imports
_IS_WINDOWS = platform.system() == "Windows"
if not _IS_WINDOWS:
    import pty
    import select
    import termios

logger = logging.getLogger("fsspec.pelican")

# Default constants for OIDC device flow (can be overridden)
DEFAULT_OIDC_TIMEOUT_SECONDS = 300  # 5 minutes
DEFAULT_PTY_BUFFER_SIZE = 1024
DEFAULT_SELECT_TIMEOUT = 0.1  # 100ms for responsive I/O

_JWT_PATTERN = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
# A prompt line ends with ":" (pelican prints it with or without a trailing newline).
# Prose such as "asked for this password whenever a new session is started." must not match.
_PASSWORD_PROMPT_PATTERN = re.compile(r"[Pp]assword[^:\n]*:\s*$")
_DEVICE_URL_PATTERN = re.compile(r"https?://\S+/(?:device|activate|authorize|login|oauth)\S*")


def _redact(text: str) -> str:
    return _JWT_PATTERN.sub("[TOKEN_REDACTED]", text)


def _running_in_ipykernel() -> bool:
    """True inside a Jupyter kernel (ZMQ shell or a subclass), False in a terminal or plain IPython."""
    ipython = sys.modules.get("IPython")
    if ipython is None:
        return False
    shell = ipython.get_ipython()
    return shell is not None and any(cls.__name__ == "ZMQInteractiveShell" for cls in type(shell).__mro__)


def _send_password(master_fd: int) -> None:
    """Ask through getpass (a masked box in notebooks) and answer the prompt on the pty."""
    password = getpass.getpass("")
    os.write(master_fd, (password + "\n").encode())


def _display_device_url(line: str, url: str) -> None:
    """Show the URL line once: a clickable link in notebook frontends, plain text elsewhere."""
    try:
        from IPython.display import display  # noqa: PLC0415
    except ImportError:
        print(line, end="", flush=True)  # noqa: T201
        return
    text = line.strip()
    link = f'<a href="{html.escape(url, quote=True)}" target="_blank">{html.escape(url)}</a>'
    display({"text/html": html.escape(text).replace(html.escape(url), link), "text/plain": text}, raw=True)


def _echo_terminal(text: str) -> None:
    """Write child output to the terminal as it arrives, with tokens redacted."""
    text = _redact(text)
    try:
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
    except AttributeError:
        sys.stdout.write(text)
        sys.stdout.flush()


class _KernelEcho:
    """
    Line-based echo for a notebook cell.

    Answers the password prompt through getpass, shows the device URL as a link,
    and drops lines that contain only the token.
    """

    def __init__(self, master_fd: int) -> None:
        self.master_fd = master_fd
        self.pending = ""  # text read but not yet printed (no trailing newline)

    def __call__(self, text: str) -> None:
        self.pending += text
        lines = self.pending.splitlines(keepends=True)
        self.pending = "" if lines[-1].endswith("\n") else lines.pop()
        if _PASSWORD_PROMPT_PATTERN.search(self.pending):  # pelican may write the prompt without a newline
            lines.append(self.pending)
            self.pending = ""
        for line in lines:
            if match := _DEVICE_URL_PATTERN.search(line):
                _display_device_url(line, match.group(0))
            elif _JWT_PATTERN.fullmatch(line.strip()):
                continue
            else:
                print(_redact(line), end="", flush=True)  # noqa: T201
                if _PASSWORD_PROMPT_PATTERN.search(line):
                    _send_password(self.master_fd)

    def flush(self) -> None:
        if self.pending and not _JWT_PATTERN.fullmatch(self.pending.strip()):
            print(_redact(self.pending), flush=True)  # noqa: T201
        self.pending = ""


def _disable_echo(fd: int) -> None:
    """Pelican turns echo off itself; do it here too so a password never lands in the output."""
    try:
        attrs = termios.tcgetattr(fd)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except termios.error as err:
        logger.debug("Could not disable echo on the pty: %s", err)


def _read_redirected_stdin() -> bytes | None:
    """Data on a non-tty stdin (such as a script run with `< answers`), to forward to the child once."""
    try:
        if os.isatty(sys.stdin.fileno()):
            return None
    except (AttributeError, io.UnsupportedOperation, ValueError):
        pass
    try:
        data = sys.stdin.read()
    except Exception as err:
        logger.debug("Could not read from redirected stdin: %s", err)
        return None
    if not data:
        return None
    logger.debug("Read %s chars from redirected stdin", len(data))
    return data.encode("utf-8")


def _reap(process: subprocess.Popen) -> None:
    """Stop the child if it still runs (timeout, interrupt) and collect its exit status."""
    if process.returncode is None:
        process.kill()
    process.wait()


def get_token_from_file(token_location: str) -> str:
    logger.debug("Opening token file: %s", token_location)
    try:
        with open(token_location) as f:
            token_contents = f.read()
    except Exception as err:
        logger.error("Error reading from token file: %s", err)
        raise

    token_str = token_contents.strip()

    # Check if the token is empty or whitespace only
    if not token_str:
        logger.warning("Token file %s is empty or contains only whitespace", token_location)
        msg = f"Token file {token_location} is empty"
        raise ValueError(msg)

    if token_str.startswith("{"):
        try:
            token_parsed = json.loads(token_contents)
            access_key = token_parsed.get("access_token")
            if access_key:
                return access_key
            logger.debug("JSON token does not contain 'access_token' key, returning full token string")
            return token_str
        except json.JSONDecodeError as err:
            logger.debug("Unable to unmarshal file %s as JSON (assuming it is a token instead): %s", token_location, err)
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
        cred_locations (list[str]): Token file paths discovered via HTCondor fallback.
        index (int): Internal index of the current fallback cred_location
    """

    location: str | None = None
    name: str | None = None
    operation: object | None = None
    destination_url: str | None = None
    pelican_url: str | None = None
    oidc_timeout_seconds: int = DEFAULT_OIDC_TIMEOUT_SECONDS
    pty_buffer_size: int = DEFAULT_PTY_BUFFER_SIZE
    select_timeout: float = DEFAULT_SELECT_TIMEOUT
    method_index: int = 0
    cred_locations: list[str] = field(default_factory=list)
    fallback_index: int = 0

    def _pelican_binary_exists(self) -> bool:
        """Check if pelican binary exists in PATH"""
        logger.debug("Checking for pelican binary in PATH: %s", os.environ.get("PATH", "(not set)"))
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
            from pelicanfs.token_generator import TokenOperation  # noqa: PLC0415

            if self.operation in [TokenOperation.TokenRead, TokenOperation.TokenSharedRead]:
                flags.append("-r")
            elif self.operation in [TokenOperation.TokenWrite, TokenOperation.TokenSharedWrite]:
                flags.append("-w")
            else:
                flags.append("-r")  # default to read

        return flags

    def _get_token_from_pelican_binary(self) -> str | None:
        """
        Invoke pelican binary to get token via OIDC device flow.

        - Unix terminal: pty for output, stdin from /dev/tty
        - Unix Jupyter kernel: pty for stdin too, password via getpass, URL as a link
        - Windows: subprocess with inherited stdin

        Returns:
            str: JWT token if successful, None otherwise
        """

        if not self.pelican_url:
            logger.warning("Cannot invoke pelican binary without pelican URL")
            return None

        flags = self._get_pelican_flag()
        cmd = ["pelican", "token", "fetch", self.pelican_url, *flags]

        logger.info("Invoking OIDC device flow via pelican binary: %s", " ".join(cmd))

        try:
            if _IS_WINDOWS:
                return self._run_pelican_windows(cmd)
            return self._run_pelican_pty(cmd, in_kernel=_running_in_ipykernel())
        except Exception as err:
            logger.debug("Error invoking pelican binary: %s", err, exc_info=True)
            return None

    def _run_pelican_windows(self, cmd: list[str]) -> str | None:
        """Inherit the console so pelican handles its prompts; capture output through a pipe."""
        process = subprocess.Popen(cmd, stdin=None, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        assert process.stdout is not None
        output: list[str] = []
        start = time.monotonic()
        try:
            while True:
                if time.monotonic() - start > self.oidc_timeout_seconds:
                    logger.warning("Pelican binary timed out (exceeded %s seconds)", self.oidc_timeout_seconds)
                    return None
                data = process.stdout.read(self.pty_buffer_size)
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                output.append(text)
                _echo_terminal(text)
            return self._finish(process, "".join(output))
        finally:
            _reap(process)

    def _run_pelican_pty(self, cmd: list[str], in_kernel: bool) -> str | None:
        """
        Run pelican under a pty (Unix).

        In a terminal the child reads stdin from /dev/tty, so pelican handles the
        password prompt itself. In a kernel, or when /dev/tty is unavailable, stdin
        is the pty: the kernel answers the prompt through getpass, a script forwards
        its redirected stdin once.
        """
        master_fd, slave_fd = pty.openpty()
        tty_fd = None
        process = None
        try:
            if not in_kernel:
                try:
                    tty_fd = os.open("/dev/tty", os.O_RDWR)
                except OSError as err:
                    logger.debug("/dev/tty unavailable, stdin goes through the pty: %s", err)
            use_tty = tty_fd is not None
            if not use_tty:
                _disable_echo(slave_fd)
            # A new session keeps the kernel's SIGINT away from pelican; _reap stops it instead.
            process = subprocess.Popen(cmd, stdin=tty_fd if use_tty else slave_fd, stdout=slave_fd, stderr=slave_fd, start_new_session=in_kernel)
            os.close(slave_fd)
            slave_fd = None
            if tty_fd is not None:
                os.close(tty_fd)
                tty_fd = None

            if in_kernel:
                echo = _KernelEcho(master_fd)
                stdin_data = None
            else:
                echo = _echo_terminal
                stdin_data = None if use_tty else _read_redirected_stdin()

            output = self._pump_pty(master_fd, process, echo, stdin_data)
            if isinstance(echo, _KernelEcho):
                echo.flush()
            if output is None:
                return None
            return self._finish(process, output)
        finally:
            os.close(master_fd)
            for fd in (slave_fd, tty_fd):
                if fd is not None:
                    os.close(fd)
            if process is not None:
                _reap(process)

    def _pump_pty(self, master_fd: int, process: subprocess.Popen, echo: Callable[[str], None], stdin_data: bytes | None) -> str | None:
        """Relay child output until it exits. Returns the full output, or None on timeout."""
        output: list[str] = []
        start = time.monotonic()

        def read() -> bytes:
            ready, _, _ = select.select([master_fd], [], [], self.select_timeout)
            if not ready:
                return b""
            try:
                return os.read(master_fd, self.pty_buffer_size)
            except OSError:  # EIO once the child closes the slave
                return b""

        def handle(data: bytes) -> None:
            text = data.decode("utf-8", errors="replace")
            output.append(text)
            echo(text)

        while True:
            if time.monotonic() - start > self.oidc_timeout_seconds:
                logger.warning("Pelican binary timed out (exceeded %s seconds)", self.oidc_timeout_seconds)
                return None
            exited = process.poll() is not None  # checked first so output written just before exit is read
            if data := read():
                handle(data)
            elif exited:
                break
            if stdin_data:
                os.write(master_fd, stdin_data)
                stdin_data = None
        return "".join(output)

    def _finish(self, process: subprocess.Popen, output: str) -> str | None:
        process.wait()
        if process.returncode != 0:
            logger.debug("Pelican binary exited with code %s", process.returncode)
            return None
        return self._extract_token(output)

    def _extract_token(self, full_output: str) -> str | None:
        """Return the last JWT in the pelican binary output, or None."""
        matches = _JWT_PATTERN.findall(full_output)
        if matches:
            logger.info("Successfully acquired token via OIDC device flow")
            return matches[-1]
        logger.warning("Could not extract JWT token from pelican binary output")
        logger.debug("Output was: %s", _redact(full_output))
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

    def __post_init__(self) -> None:
        self.methods = list(TokenDiscoveryMethod)
        # Ensure HTCONDOR_FALLBACK is always available after HTCONDOR_DISCOVERY
        if TokenDiscoveryMethod.HTCONDOR_DISCOVERY in self.methods and TokenDiscoveryMethod.HTCONDOR_FALLBACK not in self.methods:
            # Find the index of HTCONDOR_DISCOVERY and insert HTCONDOR_FALLBACK after it
            discovery_index = self.methods.index(TokenDiscoveryMethod.HTCONDOR_DISCOVERY)
            self.methods.insert(discovery_index + 1, TokenDiscoveryMethod.HTCONDOR_FALLBACK)

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> str:
        while self.method_index < len(self.methods):
            method = self.methods[self.method_index]
            self.method_index += 1
            logger.debug("Trying token discovery method: %s", method)

            match method:
                case TokenDiscoveryMethod.LOCATION:
                    if self.location:
                        logger.debug("Using API-specified token location: %s", self.location)
                        try:
                            if os.path.exists(self.location) and os.access(self.location, os.R_OK):
                                return get_token_from_file(self.location)
                            msg = f"File {self.location} is not readable"
                            raise OSError(msg)
                        except Exception as err:
                            logger.warning("Token file at %s is not readable: %s", self.location, err)

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
                            msg = f"File {token_file} is not readable"
                            raise OSError(msg)
                        except Exception as err:
                            logger.warning("Could not read BEARER_TOKEN_FILE: %s", err)

                case TokenDiscoveryMethod.DEFAULT_BEARER_TOKEN:
                    token_file = default_bearer_token_file()
                    if os.path.exists(token_file):
                        logger.debug("Using token from default bearer token file: %s", token_file)
                        try:
                            token = get_token_from_file(token_file)
                            logger.debug("Successfully read token from default file: %s...", token[:30] if token else "None")
                            return token
                        except Exception as err:
                            logger.warning("Could not read default bearer token: %s", err)

                case TokenDiscoveryMethod.ENV_TOKEN_PATH:
                    token_path = os.getenv("TOKEN")
                    if token_path:
                        if not os.path.exists(token_path):
                            logger.warning("Environment variable TOKEN is set, but file does not exist: %s", token_path)
                        else:
                            try:
                                logger.debug("Using token from TOKEN environment variable")
                                return get_token_from_file(token_path)
                            except Exception as err:
                                logger.warning("Error reading token from %s: %s", token_path, err)

                case TokenDiscoveryMethod.HTCONDOR_DISCOVERY:
                    if self.name:
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
                                logger.warning("Failed to read fallback token at %s: %s", token_path, err)
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

    def discoverHTCondorTokenLocations(self, tokenName: str) -> list[str]:
        """
        Discover possible HTCondor token file locations based on a logical token name.

        Supports environment variable _CONDOR_CREDS or defaults to `.condor_creds` in the
        current directory. If the token name includes dots, will try replacing them with
        underscores as HTCondor may sanitize filenames that way.

        Args:
            tokenName (str): Logical name of the token.

        Returns:
            list[str]: List of possible token file paths to try.
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
                logger.warning("Environment variable _CONDOR_CREDS is set, but the credential file is not readable: %s", tokenPath)
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
            logger.warning("Failure when iterating through directory to look through tokens: %s", err)

        return tokenLocations
