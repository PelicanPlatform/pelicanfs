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
from pelicanfs.core import PelicanFileSystem

def test_remove_hostname():
    """
    Test removing the hostname from various paths
    """
    # Test a single string
    paths = "https://test-url.org/namespace/path"
    assert PelicanFileSystem._remove_host_from_paths(paths) == "/namespace/path" # pylint: disable=protected-access

    # Test a list
    paths = ["https://test-url.org/namespace/path", "osdf://test-url.org/namespace/path2"]
    assert PelicanFileSystem._remove_host_from_paths(paths) == \
        ["/namespace/path", "/namespace/path2"] # pylint: disable=protected-access

    # Test an info-return
    paths = [{"name": "https://test-url.org/namespace/path",
              "other": "https://body-remains.test"},
              {"name": "pelican://test-url.org/namespace/path2", "size": "42"}]
    expected_result = [{"name": "/namespace/path",
                        "other": "https://body-remains.test"},
                        {"name": "/namespace/path2", "size": "42"}]
    assert PelicanFileSystem._remove_host_from_paths(paths) == expected_result # pylint: disable=protected-access

    # Test a find-return
    paths = {"https://test-url.org/namespace/path":
             "https://test-url2.org/namespace/path",
             "https://test-url.org/namespace/path2":
             "/namespace/path3"}
    expected_result = {"/namespace/path": "/namespace/path", "/namespace/path2": "/namespace/path3"}
    assert PelicanFileSystem._remove_host_from_paths(paths) == expected_result # pylint: disable=protected-access

    # Test a a non-list | string | dict
    assert PelicanFileSystem._remove_host_from_paths(22) == 22 # pylint: disable=protected-access
