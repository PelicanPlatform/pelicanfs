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

import fsspec
import pytest

from pelicanfs.core import InvalidMetadata, OSDFFileSystem, PelicanFileSystem, PelicanMap


def test_remove_hostname():
    # Test a single string
    paths = "https://test-url.org/namespace/path"
    assert PelicanFileSystem._remove_host_from_paths(paths) == "/namespace/path"

    # Test a list
    paths = ["https://test-url.org/namespace/path", "osdf://test-url.org/namespace/path2"]
    assert PelicanFileSystem._remove_host_from_paths(paths) == ["/namespace/path", "/namespace/path2"]

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
    ("discovery_url", "input_path", "expected_path", "expected_discovery"),
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
        # osdf://host/path keeps the trailing slash, like osdf:///path
        ("", "osdf:///namespace/dir/", "/namespace/dir/", "pelican://osg-htc.org/"),
        ("", "osdf://namespace/dir/", "/namespace/dir/", "pelican://osg-htc.org/"),
        ("", "osdf://namespace/dir", "/namespace/dir", "pelican://osg-htc.org/"),
        # Fresh filesystem receiving pelican:// should set discovery from URL
        ("", "pelican://new-discovery-url.org/p/", "/p/", "pelican://new-discovery-url.org/"),
    ],
)
def test_fspath(discovery_url, input_path, expected_path, expected_discovery):
    pelfs = PelicanFileSystem(discovery_url, skip_instance_cache=True) if discovery_url else PelicanFileSystem(skip_instance_cache=True)
    assert pelfs._check_fspath(input_path) == expected_path
    assert pelfs.discovery_url == expected_discovery


@pytest.mark.parametrize(
    ("discovery_url", "input_path"),
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


@pytest.mark.parametrize(
    ("discovery_url", "url"),
    [
        ("", "osdf://ns/obj"),
        ("", "osdf:///ns/obj"),
        ("", "osdf://ns/dir/"),
        ("", "osdf://ns"),
        ("", "pelican://host/ns/obj"),
        ("", "pelican://host:8443/ns/obj"),
        ("", "pelican://user:pass@host/ns/obj"),
        ("", "pelican://host"),
        ("", "pelican://host/"),
        ("", "pelican://host:8443"),
        ("", "host/ns/obj"),
        ("pelican://host", "/ns/obj"),
        ("pelican://osg-htc.org", "osdf://ns/obj"),
        ("pelican://host:8443", "pelican://host:8443/ns/obj"),
    ],
)
def test_strip_protocol_agrees_with_check_fspath(discovery_url, url):
    direct = PelicanFileSystem(discovery_url, skip_instance_cache=True)
    stripped = PelicanFileSystem(discovery_url, skip_instance_cache=True)

    once = PelicanFileSystem._strip_protocol(url)

    assert stripped._check_fspath(once) == direct._check_fspath(url)
    assert stripped.discovery_url == direct.discovery_url
    assert PelicanFileSystem._strip_protocol(once) == once


@pytest.mark.parametrize("url", ["osdf://ns/obj", "pelican://other.org/ns/obj"])
def test_strip_protocol_keeps_the_federation(url):
    pelfs = PelicanFileSystem("pelican://host", skip_instance_cache=True)
    with pytest.raises(InvalidMetadata):
        pelfs._check_fspath(PelicanFileSystem._strip_protocol(url))


def test_strip_protocol_list():
    assert PelicanFileSystem._strip_protocol(["osdf://ns/a", "/ns/b"]) == ["osdf:///ns/a", "/ns/b"]
    assert OSDFFileSystem._strip_protocol(["osdf://ns/a", "/ns/b"]) == ["/ns/a", "/ns/b"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("osdf://ns/obj", "/ns/obj"),
        ("osdf:///ns/obj", "/ns/obj"),
        ("osdf:///ns/obj?authz=token", "/ns/obj?authz=token"),
        ("/ns/obj", "/ns/obj"),
        ("pelican://osg-htc.org/ns/obj", "pelican://osg-htc.org/ns/obj"),
    ],
)
def test_osdf_strip_protocol(url, expected):
    assert OSDFFileSystem._strip_protocol(url) == expected
    assert OSDFFileSystem._strip_protocol(expected) == expected


@pytest.mark.parametrize(
    ("url", "expected_path", "expected_discovery"),
    [
        ("pelican://host:8443/ns/obj", "/ns/obj", "pelican://host:8443/"),
        ("pelican://host/ns/obj", "/ns/obj", "pelican://host/"),
        ("osdf://ns/obj", "/ns/obj", "pelican://osg-htc.org/"),
    ],
)
def test_url_to_fs(url, expected_path, expected_discovery):
    fs, path = fsspec.core.url_to_fs(url, skip_instance_cache=True)
    assert fs._check_fspath(path) == expected_path
    assert fs.discovery_url == expected_discovery


@pytest.mark.parametrize("path", ["pelican://host", "pelican://host/", "pelican://host:8443", "host", ""])
def test_fspath_root(path):
    pelfs = PelicanFileSystem("pelican://host:8443" if "8443" in path else "pelican://host", skip_instance_cache=True)
    assert pelfs._check_fspath(path) == "/"


def test_fspath_empty_needs_a_federation():
    with pytest.raises(InvalidMetadata):
        PelicanFileSystem(skip_instance_cache=True)._check_fspath("")


@pytest.mark.parametrize(
    ("discovery_url", "root", "expected"),
    [
        ("pelican://osg-htc.org", "osdf:///ns/store", "/ns/store"),
        ("pelican://host:8443", "pelican://host:8443/ns/store", "/ns/store"),
        ("pelican://host", "host/ns/store", "/ns/store"),
        ("pelican://host", "/ns/store", "/ns/store"),
        ("pelican://host", "", "/"),
    ],
)
def test_mapper_root_is_a_namespace_path(discovery_url, root, expected):
    pelfs = PelicanFileSystem(discovery_url, skip_instance_cache=True)
    mapper = PelicanMap(root, pelfs=pelfs)
    assert mapper.root == expected
    # FSMap turns listed paths into keys by slicing off the root
    assert mapper._str_to_key(f"{expected.rstrip('/')}/key") == "key"
    assert mapper._key_to_str("key") == f"{expected.rstrip('/')}/key"
