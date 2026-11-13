"""
Copyright (C) 2024, Pelican Project, Morgridge Institute for Research

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


class PelicanException(RuntimeError):
    """
    Base class for all Pelican-related failures
    """


class BadDirectorResponse(PelicanException):
    """
    The director response did not include the proper headers
    """


class NoAvailableSource(PelicanException):
    """
    No source endpoint is currently available for the requested object
    """


class DirectorError(NoAvailableSource):
    """
    The director answered with an error status, so it named no source
    """


class InvalidMetadata(PelicanException):
    """
    No Pelican metadata was found for the federation
    """


class NoCollectionsUrl(PelicanException):
    """
    No collections URL was found in the director response
    """


class InvalidDestinationURL(PelicanException):
    """
    The destination URL is invalid or has an empty path
    """


class TokenIteratorException(PelicanException):
    """
    Failed to fetch tokens due to iterator error
    """


class NoCredentialsException(PelicanException):
    """
    Credential is required but was not discovered
    """


class ChecksumMismatch(PelicanException):
    """
    The checksum of the transferred data differs from the checksum the server reports
    """

    def __init__(self, url: str, algorithm: str, local: str, remote: str) -> None:
        super().__init__(f"{algorithm} checksum mismatch for {url}: local {local}, server {remote}")
        self.url = url
        self.algorithm = algorithm
        self.local = local
        self.remote = remote


class ChecksumMissing(PelicanException):
    """
    Checksum verification is required, but the server reported no usable checksum
    """
