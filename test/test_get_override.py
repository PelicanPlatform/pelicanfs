"""
Copyright (C) 2026, Pelican Project, Morgridge Institute for Research

Licensed under the Apache License, Version 2.0 (the "License"); you
may not use this file except in compliance with the License.  You may
obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Regression tests for the PelicanFileSystem._get override. These exercise the
override's own logic (list-input handling, directory filtering, and destination
mapping) by mocking the network-facing collaborators, so no live server is
needed.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import pelicanfs.core


@pytest.fixture
def pelfs():
    """A PelicanFileSystem with all network-facing collaborators mocked out."""
    fs = pelicanfs.core.PelicanFileSystem(skip_instance_cache=True)

    # get_working_cache / token generation are covered elsewhere; stub them so
    # _get exercises only its own path-mapping/filtering logic.
    fs._handle_token_generation = AsyncMock(return_value=None)
    # Pass results through untouched so assertions can inspect real paths.
    fs._remove_host_from_paths = MagicMock(side_effect=lambda paths, inplace=False: paths)

    fs.http_file_system._get_file = AsyncMock(return_value=None)
    return fs


def _get_file_dest_paths(fs):
    """The local paths (lp) _get_file was asked to write, one per call."""
    return [call.args[1] for call in fs.http_file_system._get_file.call_args_list]


def _get_file_source_paths(fs):
    """The remote paths (rp) _get_file was asked to fetch, one per call."""
    return [call.args[0] for call in fs.http_file_system._get_file.call_args_list]


def test_single_file_dest_keyed_on_source_not_data_url(pelfs, tmp_path):
    """
    17a: The destination mapping must key `exists` on the caller-supplied source
    (rpath), not on the resolved cache/origin URL. When data_url carries a query
    string (magic `?`) but the source does not, a single file downloaded to a
    plain (non-directory) destination must land exactly at that destination --
    not inside a wrongly-created subdirectory named after the object.
    """
    # data_url has a query string -> has_magic(data_url) is True (the bug trigger).
    data_url = "https://cache.example/ns/file.txt?authz=abc123"
    pelfs.get_working_cache = AsyncMock(return_value=(data_url, MagicMock()))
    pelfs.http_file_system._expand_path = AsyncMock(return_value=[data_url])
    pelfs.http_file_system._isdir = AsyncMock(return_value=False)

    dest = str(tmp_path / "out.dat")  # a plain filename, not an existing directory
    pelfs.get("pelican://fed.example.com/ns/file.txt", dest)

    dests = _get_file_dest_paths(pelfs)
    assert dests == [dest], f"expected file to land at {dest!r}, got {dests!r}"


def test_list_inputs_do_not_crash_and_route_per_file(pelfs, tmp_path):
    """
    17b: get([...], [...]) must be handled before _check_fspath (which runs
    urlparse and raises on a list). The list branch delegates to the base
    implementation, which fetches each pair via _get_file.
    """
    # Prove the list branch short-circuits before any str-only preprocessing.
    pelfs._check_fspath = MagicMock(side_effect=AssertionError("_check_fspath must not run for list inputs"))
    # The base _get iterates over self._get_file for list inputs.
    pelfs._get_file = AsyncMock(return_value=None)

    rpaths = ["pelican://fed.example.com/ns/a.txt", "pelican://fed.example.com/ns/b.txt"]
    lpaths = [str(tmp_path / "a.txt"), str(tmp_path / "b.txt")]

    pelfs.get(rpaths, lpaths)  # must not raise

    pelfs._check_fspath.assert_not_called()
    fetched = {call.args[0]: call.args[1] for call in pelfs._get_file.call_args_list}
    assert fetched == dict(zip(rpaths, lpaths))


def test_recursive_get_filters_out_directories(pelfs, tmp_path):
    """
    17c: On a recursive get, entries that are directories must be excluded so
    they are never GET-ed (cache servers answer a directory GET with 403). Only
    the files reach _get_file.
    """
    base = "https://cache.example/ns/dir"
    file_a = f"{base}/a.txt"
    sub_dir = f"{base}/sub"
    file_c = f"{base}/sub/c.txt"

    pelfs.get_working_cache = AsyncMock(return_value=(base, MagicMock()))
    pelfs.http_file_system._expand_path = AsyncMock(return_value=[file_a, sub_dir, file_c])
    # Only sub_dir is a directory.
    pelfs.http_file_system._isdir = AsyncMock(side_effect=lambda p: p == sub_dir)

    dest = str(tmp_path / "download")
    pelfs.get("pelican://fed.example.com/ns/dir", dest, recursive=True)

    sources = _get_file_source_paths(pelfs)
    assert sub_dir not in sources, "directory entry must be filtered out"
    assert set(sources) == {file_a, file_c}
    # _isdir is consulted once per non-trailing-sep candidate.
    assert pelfs.http_file_system._isdir.await_count == 3
