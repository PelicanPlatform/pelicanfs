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
from dataclasses import dataclass, field
from typing import List, Tuple

from igwn_auth_utils.scitokens import (  # adjust import paths
    _find_condor_creds_token_paths,
    default_bearer_token_file,
)

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


def get_token_from_file(token_location: str) -> str:
    log.debug(f"Opening token file: {token_location}")
    try:
        with open(token_location, "r") as f:
            token_contents = f.read()
    except Exception as err:
        log.error(f"Error reading from token file: {err}")
        raise

    token_str = token_contents.strip()

    if token_str.startswith("{"):
        try:
            token_parsed = json.loads(token_contents)
            access_key = token_parsed.get("access_token")
            if access_key:
                return access_key
            else:
                log.debug("JSON token does not contain 'access_token' key, returning full token string")
                return token_str
        except json.JSONDecodeError as err:
            log.debug(f"Unable to unmarshal file {token_location} as JSON (assuming it is a token instead): {err}")
            return token_str
    else:
        return token_str


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

    Attributes:
        location (str): Specific token file path (optional).
        name (str): Logical name of the token (used by HTCondor discovery).
        method (int): Internal index of the current discovery method.
        cred_locations (List[str]): Token file paths discovered via HTCondor fallback.
    """

    location: str
    name: str
    method: int = 0
    cred_locations: List[str] = field(default_factory=list)

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
        if "." in tokenName:
            underscoreTokenName = tokenName.replace(".", "_")
            tokenLocations = self.discoverHTCondorTokenLocations(underscoreTokenName)
            if tokenLocations:
                return tokenLocations

        credsDir = os.getenv("_CONDOR_CREDS", ".condor_creds")

        if tokenName:
            tokenPath = os.path.join(credsDir, tokenName)
            tokenUsePath = os.path.join(credsDir, f"{tokenName}.use")
            if not os.path.exists(tokenPath):
                log.warning(f"Environment variable _CONDOR_CREDS is set, but the credential file is not readable: {tokenPath}")
            else:
                tokenLocations.append(tokenUsePath)
                return tokenLocations
        else:
            scitokensUsePath = os.path.join(credsDir, "scitokens.use")
            if os.path.exists(scitokensUsePath):
                tokenLocations.append(scitokensUsePath)

        # Use _find_condor_creds_token_paths() generator to find *.use files
        try:
            for token_path in _find_condor_creds_token_paths() or []:
                baseName = os.path.basename(str(token_path))
                # Skip special files
                if baseName == "scitokens.use" or baseName.startswith("."):
                    continue
                tokenLocations.append(str(token_path))
        except Exception as err:
            log.warning(f"Failure when iterating through directory to look through tokens: {err}")

        return tokenLocations

    def next(self) -> Tuple[str, bool]:
        """
        Return the next valid token from the configured discovery sources.

        Returns:
            Tuple[str, bool]: A tuple where the first element is the token string (empty if not found),
                              and the second element is a boolean indicating success.
        """
        while True:
            match self.method:
                case 0:
                    self.method += 1
                    if self.location:
                        log.debug(f"Using API-specified token location: {self.location}")
                        try:
                            if os.path.exists(self.location) and os.access(self.location, os.R_OK):
                                jwt_serialized = get_token_from_file(self.location)
                                return jwt_serialized, True
                            else:
                                raise OSError(f"File {self.location} is not readable")
                        except Exception as err:
                            log.warning(f"Client was asked to read token from location {self.location} but it is not readable: {err}")

                case 1:
                    self.method += 1
                    bearer_token = os.getenv("BEARER_TOKEN")
                    if bearer_token is not None:
                        log.debug("Using token from BEARER_TOKEN environment variable")
                        return bearer_token, True

                case 2:
                    self.method += 1
                    bearer_token_file = os.getenv("BEARER_TOKEN_FILE")
                    if bearer_token_file:
                        log.debug("Using token from BEARER_TOKEN_FILE environment variable")
                        try:
                            if os.path.exists(bearer_token_file) and os.access(bearer_token_file, os.R_OK):
                                jwt_serialized = get_token_from_file(bearer_token_file)
                                return jwt_serialized, True
                            else:
                                raise OSError(f"File {bearer_token_file} is not readable")
                        except Exception as err:
                            log.warning(f"Environment variable BEARER_TOKEN_FILE is set, but file does not exist or is not readable: {err}")

                case 3:
                    self.method += 1
                    # Use the first_module's default token file location
                    token_path = default_bearer_token_file()
                    if os.path.exists(token_path):
                        log.debug(f"Using token from default bearer token file location: {token_path}")
                        try:
                            jwt_serialized = get_token_from_file(token_path)
                            return jwt_serialized, True
                        except Exception as err:
                            log.warning(f"Error reading token from {token_path}: {err}")

                case 4:
                    self.method += 1
                    token_file = os.getenv("TOKEN")
                    if token_file:
                        if not os.path.exists(token_file):
                            log.warning(f"Environment variable TOKEN is set, but file does not exist: {token_file}")
                        else:
                            try:
                                jwt_serialized = get_token_from_file(token_file)
                                log.debug("Using token from TOKEN environment variable")
                                return jwt_serialized, True
                            except Exception as err:
                                log.warning(f"Error reading token from {token_file}: {err}")

                case 5:
                    self.method += 1
                    self.cred_locations = self.discoverHTCondorTokenLocations(self.name)

                case _:
                    idx = self.method - 6
                    self.method += 1
                    if idx < 0 or idx >= len(self.cred_locations):
                        log.debug("Out of token locations to search")
                        return "", False
                    try:
                        jwt_serialized = get_token_from_file(self.cred_locations[idx])
                        return jwt_serialized, True
                    except Exception:
                        continue
