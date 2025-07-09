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
from typing import List, Optional
from urllib.parse import ParseResult, urlparse

"""
Classes to hold director response headers

TODO: Add more fields as needed to support other features
"""


class XPelNs:
    def __init__(self, namespace: Optional[str] = None):
        self.Namespace: Optional[str] = namespace


class XPelTokGen:
    def __init__(self, issuers: Optional[List[str]] = None):
        self.Issuers: List[ParseResult] = []

        if issuers:
            for u in issuers:
                try:
                    parsed = urlparse(u)
                    if parsed.scheme and parsed.netloc:
                        self.Issuers.append(parsed)
                except Exception:
                    # ignore malformed URLs for now
                    continue


class DirectorResponse:
    def __init__(
        self,
        xpel_ns_hdr: Optional[XPelNs] = None,
        xpel_tok_gen_hdr: Optional[XPelTokGen] = None,
    ):
        self.XPelNsHdr: XPelNs = xpel_ns_hdr if xpel_ns_hdr is not None else XPelNs()
        self.XPelTokGenHdr: XPelTokGen = xpel_tok_gen_hdr if xpel_tok_gen_hdr is not None else XPelTokGen()
