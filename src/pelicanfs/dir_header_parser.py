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

from .exceptions import BadDirectorResponse


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


def parse_metalink(headers: dict[str, str]) -> tuple[list[tuple[str, int]], str]:
    """
    Parse the metalink headers to get a list of caches to attempt to try in priority orider
    """
    linkPrio: list[tuple[str, int]] = []

    if "Link" in headers:
        links = headers["Link"].split(",")
        for mlink in links:
            elmts = mlink.split(";")
            mdict = {}
            for elm in elmts[1:]:
                left, right = elm.split("=", 1)
                mdict[left.strip()] = right.strip()

            priority = len(headers)
            if mdict["pri"]:
                priority = int(mdict["pri"])

            link = elmts[0].strip(" <>")

            linkPrio.append([link, priority])
    linkPrio.sort(key=lambda x: x[1])

    # Pull out the namespace information; we'll use this to populate
    # the namespace prefix cache later
    namespace = ""
    for info in headers.get("X-Pelican-Namespace", "").split(","):
        info = info.strip()
        pair = info.split("=", 1)
        if len(pair) < 2:
            continue
        key, val = pair
        if key == "namespace":
            namespace = val
            break

    return linkPrio, namespace
