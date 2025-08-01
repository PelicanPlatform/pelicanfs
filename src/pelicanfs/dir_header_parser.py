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

from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse

from .exceptions import BadDirectorResponse


@dataclass
class XPelAuth:
    """X-Pelican-Authorization header data"""

    # Add fields as needed based on the actual header structure
    pass


@dataclass
class XPelNs:
    """X-Pelican-Namespace header data"""

    namespace: str
    collections_url: Optional[str] = None
    require_token: bool = False


@dataclass
class XPelTokGen:
    """X-Pelican-Token-Generation header data"""

    issuers: List[str] = None

    def __post_init__(self):
        if self.issuers is None:
            self.issuers = []
        else:
            # Validate and filter issuers to ensure they are valid URLs
            validated_issuers = []
            for issuer in self.issuers:
                try:
                    parsed = urlparse(issuer)
                    if parsed.scheme and parsed.netloc:
                        validated_issuers.append(issuer)
                except Exception:
                    # ignore malformed URLs for now
                    continue
            self.issuers = validated_issuers


@dataclass
class DirectorResponse:
    """Represents a director response with all parsed headers and data"""

    object_servers: List[str]  # List of server URLs from Link header
    location: Optional[str]  # URL from Location header
    x_pel_auth_hdr: Optional[XPelAuth] = None
    x_pel_ns_hdr: Optional[XPelNs] = None
    x_pel_tok_gen_hdr: Optional[XPelTokGen] = None


def parse_director_response(headers: dict) -> DirectorResponse:
    """
    Parse director response headers into a DirectorResponse object.

    Args:
        headers: HTTP response headers from director

    Returns:
        DirectorResponse object containing all parsed data
    """
    # Parse Link header for object servers
    object_servers = []
    if "Link" in headers:
        links = headers["Link"].split(",")
        link_entries = []

        for link in links:
            parts = link.split(";")
            url_part = parts[0].strip()

            # Extract URL from <url> format
            if url_part.startswith("<") and url_part.endswith(">"):
                url = url_part[1:-1]
                priority = len(headers)  # default priority (lowest)

                # Parse attributes into a dictionary like the original parse_metalink
                attributes = {}
                for part in parts[1:]:
                    if "=" in part:
                        left, right = part.split("=", 1)
                        attributes[left.strip()] = right.strip()

                # Get priority from attributes
                if "pri" in attributes:
                    try:
                        priority = int(attributes["pri"])
                    except (ValueError, IndexError):
                        pass

                link_entries.append((url, priority))

        # Sort by priority (lower number = higher priority)
        link_entries.sort(key=lambda x: x[1])
        object_servers = [url for url, priority in link_entries]

    # Parse Location header
    location = headers.get("Location")

    # Parse X-Pelican-Namespace header
    x_pel_ns_hdr = None
    if "X-Pelican-Namespace" in headers:
        ns_header = headers["X-Pelican-Namespace"]
        # Parse namespace header
        namespace = None
        collections_url = None
        require_token = False

        # Parse the header content
        for part in ns_header.split(", "):
            if part.startswith("namespace="):
                namespace = part.split("=", 1)[1]
            elif part.startswith("collections-url="):
                collections_url = part.split("=", 1)[1]
            elif part.startswith("require-token="):
                require_token = part.split("=", 1)[1].lower() == "true"

        if namespace:
            x_pel_ns_hdr = XPelNs(namespace=namespace, collections_url=collections_url, require_token=require_token)

    # Parse X-Pelican-Authorization header (if present)
    x_pel_auth_hdr = None
    if "X-Pelican-Authorization" in headers:
        # Parse authorization header
        x_pel_auth_hdr = XPelAuth()

    # Parse X-Pelican-Token-Generation header (if present)
    x_pel_tok_gen_hdr = None
    if "X-Pelican-Token-Generation" in headers:
        # Parse token generation header
        tok_gen_header = headers["X-Pelican-Token-Generation"]
        issuers = []

        # Parse the header content for issuers
        for part in tok_gen_header.split(", "):
            if part.startswith("issuer="):
                issuer = part.split("=", 1)[1]
                issuers.append(issuer)

        x_pel_tok_gen_hdr = XPelTokGen(issuers=issuers)

    return DirectorResponse(object_servers=object_servers, location=location, x_pel_auth_hdr=x_pel_auth_hdr, x_pel_ns_hdr=x_pel_ns_hdr, x_pel_tok_gen_hdr=x_pel_tok_gen_hdr)


def get_collections_url(headers: dict[str, str]) -> str:
    """
    Get the collections URL from the director response headers
    """

    if "X-Pelican-Namespace" not in headers:
        raise BadDirectorResponse()

    for info in headers.get("X-Pelican-Namespace", "").split(","):
        info = info.strip()
        pair = info.split("=", 1)
        if len(pair) < 2:
            continue
        key, val = pair
        if key == "collections-url":
            return val

    return None
