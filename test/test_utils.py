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
import pytest

from pelicanfs.core import InvalidMetadata, PelicanFileSystem


def test_remove_hostname():
    # Test a single string
    paths = "https://test-url.org/namespace/path"
    assert PelicanFileSystem._remove_host_from_paths(paths) == "/namespace/path"

    # Test a list
    paths = ["https://test-url.org/namespace/path", "osdf://test-url.org/namespace/path2"]
    PelicanFileSystem._remove_host_from_paths(paths) == ["/namespace/path", "namespace/pathe2"]

    # Test an info-return
    paths = [
        {"name": "https://test-url.org/namespace/path", "other": "https://body-remains.test"},
        {"name": "pelican://test-url.org/namespace/path2", "size": "42"},
    ]
    expected_result = [
        {"name": "/namespace/path", "other": "https://body-remains.test"},
        {"name": "/namespace/path2", "size": "42"},
    ]
    assert PelicanFileSystem._remove_host_from_paths(paths) == expected_result

    # Test a find-return
    paths = {
        "https://test-url.org/namespace/path": "https://test-url2.org/namespace/path",
        "https://test-url.org/namespace/path2": "/namespace/path3",
    }
    expected_result = {"/namespace/path": "/namespace/path", "/namespace/path2": "/namespace/path3"}
    assert PelicanFileSystem._remove_host_from_paths(paths) == expected_result

    # Test a a non-list | string | dict
    assert PelicanFileSystem._remove_host_from_paths(22) == 22


@pytest.mark.parametrize(
    "discovery_url,input_path,expected_path,expected_discovery",
    [
        # Absolute paths pass through unchanged
        ("pelican://test-discovery-url.org", "/absolute/path", "/absolute/path", "pelican://test-discovery-url.org/"),
        # pelican:// URLs with matching discovery
        ("pelican://test-discovery-url.org", "pelican://test-discovery-url.org/p2/", "/p2/", "pelican://test-discovery-url.org/"),
        # Host-style paths (no scheme)
        ("pelican://test-discovery-url.org", "test-discovery-url.org/p3", "/p3", "pelican://test-discovery-url.org/"),
        # osdf:// URLs should work with OSDFFileSystem (discovery_url is osg-htc.org)
        ("pelican://osg-htc.org", "osdf:///namespace/path", "/namespace/path", "pelican://osg-htc.org/"),
        # Fresh filesystem receiving osdf:// should auto-configure for OSDF
        ("", "osdf:///namespace/path", "/namespace/path", "pelican://osg-htc.org/"),
        # Fresh filesystem receiving pelican:// should set discovery from URL
        ("", "pelican://new-discovery-url.org/p/", "/p/", "pelican://new-discovery-url.org/"),
    ],
)
def test_fspath(discovery_url, input_path, expected_path, expected_discovery):
    pelfs = PelicanFileSystem(discovery_url, skip_instance_cache=True) if discovery_url else PelicanFileSystem(skip_instance_cache=True)
    assert pelfs._check_fspath(input_path) == expected_path
    assert pelfs.discovery_url == expected_discovery


@pytest.mark.parametrize(
    "discovery_url,input_path",
    [
        # pelican:// URL with mismatched discovery
        ("pelican://test-discovery-url.org", "pelican://diff-disc/path"),
        # Host-style path with mismatched discovery
        ("pelican://test-discovery-url.org", "not-the-discovery-url.org/p3"),
        # osdf:// URL with non-OSDF federation should fail
        ("pelican://other-federation.org", "osdf:///namespace/path"),
    ],
)
def test_fspath_invalid(discovery_url, input_path):
    pelfs = PelicanFileSystem(discovery_url, skip_instance_cache=True)
    with pytest.raises(InvalidMetadata):
        pelfs._check_fspath(input_path)
