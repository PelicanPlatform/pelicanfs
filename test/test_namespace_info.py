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
from pelicanfs.core import PelicanFileSystem, _CacheManager


def test_cache_manager_with_namespace_token_requirement():
    """Test that _CacheManager properly stores director_response at namespace level"""
    # Test with namespace that requires token
    cache_list = ["https://cache1.example.com", "https://cache2.example.com"]
    from pelicanfs.dir_header_parser import DirectorResponse, XPelNs

    x_pel_ns = XPelNs(namespace="/test", require_token=True)
    director_response = DirectorResponse(object_servers=cache_list, location=None, x_pel_ns_hdr=x_pel_ns)
    cache_manager = _CacheManager(cache_list, director_response)
    assert cache_manager.director_response == director_response
    assert cache_manager.director_response.x_pel_ns_hdr.require_token is True

    # Test that we can get URLs normally
    url = cache_manager.get_url("/some/path")
    assert url == "https://cache1.example.com/some/path"


def test_cache_manager_without_token_requirement():
    """Test that _CacheManager works with no token requirement"""
    # Test with namespace that doesn't require token
    cache_list = ["https://cache1.example.com", "https://cache2.example.com"]
    from pelicanfs.dir_header_parser import DirectorResponse, XPelNs

    x_pel_ns = XPelNs(namespace="/test", require_token=False)
    director_response = DirectorResponse(object_servers=cache_list, location=None, x_pel_ns_hdr=x_pel_ns)
    cache_manager = _CacheManager(cache_list, director_response)
    assert cache_manager.director_response == director_response
    assert cache_manager.director_response.x_pel_ns_hdr.require_token is False

    # Test that we can get URLs normally
    url = cache_manager.get_url("/some/path")
    assert url == "https://cache1.example.com/some/path"


def test_get_prefix_info_returns_namespace_info():
    """Test that _get_prefix_info returns namespace_info with namespace-level token requirements"""
    pelfs = PelicanFileSystem(skip_instance_cache=True)

    # Mock the namespace cache to return a _CacheManager with token requirement
    cache_list = ["https://cache1.example.com", "https://cache2.example.com"]
    from pelicanfs.dir_header_parser import DirectorResponse, XPelNs

    x_pel_ns = XPelNs(namespace="/test", require_token=True)
    director_response = DirectorResponse(object_servers=cache_list, location=None, x_pel_ns_hdr=x_pel_ns)
    mock_cache_manager = _CacheManager(cache_list, director_response)
    pelfs._namespace_cache = {"/test/namespace": mock_cache_manager}

    # Test that _get_prefix_info returns the cache manager
    namespace_info = pelfs._get_prefix_info("/test/namespace/file.txt")
    assert namespace_info is not None

    # Test that the namespace requires token
    assert namespace_info.director_response.x_pel_ns_hdr.require_token is True

    # Test with a path that doesn't match any namespace
    namespace_info = pelfs._get_prefix_info("/other/namespace/file.txt")
    assert namespace_info is None


def test_parse_director_response_with_namespace_token_requirement():
    """Test that parse_director_response correctly parses requires-token at namespace level"""
    from pelicanfs.dir_header_parser import parse_director_response

    # Test headers with namespace-level token requirement
    headers = {
        "Link": '<https://cache1.example.com/test>; rel="duplicate"; pri=1, <https://cache2.example.com/test>; rel="duplicate"; pri=2',
        "X-Pelican-Namespace": "namespace=/test, collections-url=https://cache1.example.com/test, require-token=true",
    }
    director_response = parse_director_response(headers)

    # Check that we have two object servers
    assert len(director_response.object_servers) == 2

    # Check that the namespace requires token
    assert director_response.x_pel_ns_hdr is not None
    assert director_response.x_pel_ns_hdr.require_token is True
    assert director_response.x_pel_ns_hdr.namespace == "/test"

    # Check object server URLs
    assert "https://cache1.example.com/test" in director_response.object_servers
    assert "https://cache2.example.com/test" in director_response.object_servers


def test_parse_director_response_defaults_to_no_token():
    """Test that parse_director_response defaults to no token requirement when not specified"""
    from pelicanfs.dir_header_parser import parse_director_response

    # Test headers without require-token specification
    headers = {"Link": '<https://cache1.example.com/test>; rel="duplicate"; pri=1', "X-Pelican-Namespace": "namespace=/test, collections-url=https://cache1.example.com/test"}
    director_response = parse_director_response(headers)

    assert director_response.x_pel_ns_hdr is not None
    assert director_response.x_pel_ns_hdr.namespace == "/test"
    assert director_response.x_pel_ns_hdr.require_token is False  # defaults to False


def test_parse_director_response_real_director_response():
    """Test that parse_director_response correctly parses real director response format"""
    from pelicanfs.dir_header_parser import parse_director_response

    # Test with actual director response format
    headers = {
        "Link": '<https://namespace.example.com/namespace>; rel="duplicate"; pri=1; depth=3',
        "X-Pelican-Namespace": "namespace=/namespace, require-token=true, collections-url=https://namespace.example.com/namespace",
    }
    director_response = parse_director_response(headers)

    assert director_response.x_pel_ns_hdr is not None
    assert director_response.x_pel_ns_hdr.namespace == "/namespace"
    assert director_response.x_pel_ns_hdr.require_token is True  # should parse require-token=true


def test_get_origin_url_parses_token_requirements(monkeypatch):
    """Test that get_origin_url parses director response for token requirements"""
    import asyncio

    from pelicanfs.core import PelicanFileSystem

    pelfs = PelicanFileSystem(skip_instance_cache=True)

    # Mock the get_director_headers method to return headers with token requirement
    async def mock_get_director_headers(fileloc, origin=False):
        return {"Location": "https://origin.example.com/namespace/file.txt", "X-Pelican-Namespace": "namespace=/namespace, require-token=true, collections-url=https://origin.example.com/namespace"}

    # Mock the parse_director_response function
    def mock_parse_director_response(headers):
        from pelicanfs.dir_header_parser import DirectorResponse, XPelNs

        x_pel_ns = XPelNs(namespace="/namespace", require_token=True)
        return DirectorResponse(object_servers=["https://origin.example.com/namespace"], location="https://origin.example.com/namespace/file.txt", x_pel_ns_hdr=x_pel_ns)

    # Apply the mocks using monkeypatch
    # Store original methods to restore later
    original_get_director_headers = pelfs.get_director_headers

    pelfs.get_director_headers = mock_get_director_headers
    monkeypatch.setattr("pelicanfs.dir_header_parser.parse_director_response", mock_parse_director_response)

    try:
        # Test that get_origin_url returns the origin URL
        origin_url, director_response = asyncio.run(pelfs.get_origin_url("/namespace/file.txt"))
        assert origin_url == "https://origin.example.com/namespace/file.txt"
        assert director_response.x_pel_ns_hdr.require_token is True

        # Test that no namespace information was stored in the cache since origin URLs don't use cache manager
        namespace_info = pelfs._get_prefix_info("/namespace/file.txt")
        assert namespace_info is None
    finally:
        # Restore original methods
        pelfs.get_director_headers = original_get_director_headers


def test_get_origin_url_no_token_requirement(monkeypatch):
    """Test that get_origin_url handles cases where no token is required"""
    import asyncio

    from pelicanfs.core import PelicanFileSystem

    pelfs = PelicanFileSystem(skip_instance_cache=True)

    # Mock the get_director_headers method to return headers without token requirement
    async def mock_get_director_headers(fileloc, origin=False):
        return {"Location": "https://origin.example.com/namespace/file.txt", "X-Pelican-Namespace": "namespace=/namespace, collections-url=https://origin.example.com/namespace"}

    # Mock the parse_director_response function
    def mock_parse_director_response(headers):
        from pelicanfs.dir_header_parser import DirectorResponse, XPelNs

        x_pel_ns = XPelNs(namespace="/namespace", require_token=False)
        return DirectorResponse(object_servers=["https://origin.example.com/namespace"], location="https://origin.example.com/namespace/file.txt", x_pel_ns_hdr=x_pel_ns)

    # Apply the mocks using monkeypatch
    # Store original methods to restore later
    original_get_director_headers = pelfs.get_director_headers

    pelfs.get_director_headers = mock_get_director_headers
    monkeypatch.setattr("pelicanfs.dir_header_parser.parse_director_response", mock_parse_director_response)

    try:
        # Test that get_origin_url returns the origin URL
        origin_url, director_response = asyncio.run(pelfs.get_origin_url("/namespace/file.txt"))
        assert origin_url == "https://origin.example.com/namespace/file.txt"
        assert director_response.x_pel_ns_hdr.require_token is False

        # Test that no namespace information was stored in the cache since origin URLs don't use cache manager
        namespace_info = pelfs._get_prefix_info("/namespace/file.txt")
        assert namespace_info is None
    finally:
        # Restore original methods
        pelfs.get_director_headers = original_get_director_headers


def test_get_dirlist_url_parses_token_requirements(monkeypatch):
    """Test that get_dirlist_url parses director response for token requirements"""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from pelicanfs.core import PelicanFileSystem

    pelfs = PelicanFileSystem(skip_instance_cache=True)

    # Mock the _set_director_url method
    async def mock_set_director_url():
        pelfs.director_url = "https://director.example.com/"

    # Mock the http_file_system.set_session method
    mock_session = MagicMock()
    mock_session.headers = {}
    # Store the original method to restore later
    original_set_session = pelfs.http_file_system.set_session
    pelfs.http_file_system.set_session = AsyncMock(return_value=mock_session)

    # Mock the session.request method to return a proper async context manager
    mock_response = MagicMock()
    mock_response.headers = {
        "Link": '<https://collections.example.com/namespace>; rel="collection"; pri=1',
        "X-Pelican-Namespace": "namespace=/namespace, require-token=true, collections-url=https://collections.example.com/namespace",
    }

    # Create a proper async context manager mock
    class MockAsyncContextManager:
        def __init__(self, response):
            self.response = response

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_session.request = MagicMock(return_value=MockAsyncContextManager(mock_response))

    # Mock the get_collections_url function
    def mock_get_collections_url(headers):
        return "https://collections.example.com/namespace"

    # Mock the parse_director_response function
    def mock_parse_director_response(headers):
        from pelicanfs.dir_header_parser import DirectorResponse, XPelNs

        x_pel_ns = XPelNs(namespace="/namespace", require_token=True)
        return DirectorResponse(object_servers=["https://collections.example.com/namespace"], location="https://collections.example.com/namespace/file.txt", x_pel_ns_hdr=x_pel_ns)

    # Apply the mocks using monkeypatch
    # Store original methods to restore later
    original_set_director_url = pelfs._set_director_url
    original_director_url = pelfs.director_url

    pelfs._set_director_url = mock_set_director_url
    monkeypatch.setattr("pelicanfs.dir_header_parser.get_collections_url", mock_get_collections_url)
    monkeypatch.setattr("pelicanfs.dir_header_parser.parse_director_response", mock_parse_director_response)

    try:
        # Test that get_dirlist_url returns the dirlist URL
        dirlist_url, director_response = asyncio.run(pelfs.get_dirlist_url("/namespace/file.txt"))
        assert dirlist_url == "https://collections.example.com/namespace/file.txt"
        assert director_response.x_pel_ns_hdr.require_token is True
    finally:
        # Restore the original HTTP client method and other instance modifications
        pelfs.http_file_system.set_session = original_set_session
        pelfs._set_director_url = original_set_director_url
        pelfs.director_url = original_director_url


def test_get_dirlist_url_no_token_requirement(monkeypatch):
    """Test that get_dirlist_url handles cases where no token is required"""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from pelicanfs.core import PelicanFileSystem

    pelfs = PelicanFileSystem(skip_instance_cache=True)

    # Mock the _set_director_url method
    async def mock_set_director_url():
        pelfs.director_url = "https://director.example.com/"

    # Mock the http_file_system.set_session method
    mock_session = MagicMock()
    mock_session.headers = {}
    # Store the original method to restore later
    original_set_session = pelfs.http_file_system.set_session
    pelfs.http_file_system.set_session = AsyncMock(return_value=mock_session)

    # Mock the session.request method to return a response with headers
    mock_response = MagicMock()
    mock_response.headers = {
        "Link": '<https://collections.example.com/namespace>; rel="collection"; pri=1',
        "X-Pelican-Namespace": "namespace=/namespace, collections-url=https://collections.example.com/namespace",
    }

    # Create a proper async context manager mock
    class MockAsyncContextManager:
        def __init__(self, response):
            self.response = response

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_session.request = MagicMock(return_value=MockAsyncContextManager(mock_response))

    # Mock the get_collections_url function
    def mock_get_collections_url(headers):
        return "https://collections.example.com/namespace"

    # Mock the parse_director_response function
    def mock_parse_director_response(headers):
        from pelicanfs.dir_header_parser import DirectorResponse, XPelNs

        x_pel_ns = XPelNs(namespace="/namespace", require_token=False)
        return DirectorResponse(object_servers=["https://collections.example.com/namespace"], location="https://collections.example.com/namespace/file.txt", x_pel_ns_hdr=x_pel_ns)

    # Apply the mocks using monkeypatch
    # Store original methods to restore later
    original_set_director_url = pelfs._set_director_url
    original_director_url = pelfs.director_url

    pelfs._set_director_url = mock_set_director_url
    monkeypatch.setattr("pelicanfs.dir_header_parser.get_collections_url", mock_get_collections_url)
    monkeypatch.setattr("pelicanfs.dir_header_parser.parse_director_response", mock_parse_director_response)

    try:
        # Test that get_dirlist_url returns the dirlist URL
        dirlist_url, director_response = asyncio.run(pelfs.get_dirlist_url("/namespace/file.txt"))
        assert dirlist_url == "https://collections.example.com/namespace/file.txt"
        assert director_response.x_pel_ns_hdr.require_token is False
    finally:
        # Restore the original HTTP client method and other instance modifications
        pelfs.http_file_system.set_session = original_set_session
        pelfs._set_director_url = original_set_director_url
        pelfs.director_url = original_director_url


def test_parse_director_response_with_token_generation():
    """Test that parse_director_response correctly parses X-Pelican-Token-Generation header"""
    from pelicanfs.dir_header_parser import parse_director_response

    # Test headers with token generation information
    headers = {
        "Link": '<https://cache1.example.com/test>; rel="duplicate"; pri=1',
        "X-Pelican-Namespace": "namespace=/test, collections-url=https://cache1.example.com/test, require-token=true",
        "X-Pelican-Token-Generation": "issuer=https://trusted-issuer1.example.com, issuer=https://trusted-issuer2.example.com",
    }

    director_response = parse_director_response(headers)

    # Check that we have the token generation header
    assert director_response.x_pel_tok_gen_hdr is not None
    assert len(director_response.x_pel_tok_gen_hdr.issuers) == 2
    assert "https://trusted-issuer1.example.com" in director_response.x_pel_tok_gen_hdr.issuers
    assert "https://trusted-issuer2.example.com" in director_response.x_pel_tok_gen_hdr.issuers

    # Check that namespace info is also parsed
    assert director_response.x_pel_ns_hdr is not None
    assert director_response.x_pel_ns_hdr.namespace == "/test"
    assert director_response.x_pel_ns_hdr.require_token


def test_parse_director_response_without_token_generation():
    """Test that parse_director_response handles missing X-Pelican-Token-Generation header"""
    from pelicanfs.dir_header_parser import parse_director_response

    # Test headers without token generation information
    headers = {"Link": '<https://cache1.example.com/test>; rel="duplicate"; pri=1', "X-Pelican-Namespace": "namespace=/test, collections-url=https://cache1.example.com/test"}

    director_response = parse_director_response(headers)

    # Check that token generation header is None
    assert director_response.x_pel_tok_gen_hdr is None

    # Check that namespace info is still parsed
    assert director_response.x_pel_ns_hdr is not None
    assert director_response.x_pel_ns_hdr.namespace == "/test"


def test_parse_director_response_with_malformed_issuer_urls():
    """Test that parse_director_response filters out malformed issuer URLs"""
    from pelicanfs.dir_header_parser import parse_director_response

    # Test headers with both valid and malformed issuer URLs
    headers = {
        "Link": '<https://cache1.example.com/test>; rel="duplicate"; pri=1',
        "X-Pelican-Namespace": "namespace=/test, collections-url=https://cache1.example.com/test, require-token=true",
        "X-Pelican-Token-Generation": "issuer=https://trusted-issuer1.example.com, issuer=invalid-url, issuer=https://trusted-issuer2.example.com, issuer=not-a-url",
    }

    director_response = parse_director_response(headers)

    # Check that we have the token generation header
    assert director_response.x_pel_tok_gen_hdr is not None
    # Should only have the valid URLs
    assert len(director_response.x_pel_tok_gen_hdr.issuers) == 2
    assert "https://trusted-issuer1.example.com" in director_response.x_pel_tok_gen_hdr.issuers
    assert "https://trusted-issuer2.example.com" in director_response.x_pel_tok_gen_hdr.issuers
    # Malformed URLs should be filtered out
    assert "invalid-url" not in director_response.x_pel_tok_gen_hdr.issuers
    assert "not-a-url" not in director_response.x_pel_tok_gen_hdr.issuers
