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

import aiohttp
import pytest
import pelicanfs.core
from pelicanfs.core import _AccessResp, _AccessStats, PelicanException, PelicanFileSystem, NoAvailableSource
import ssl
import trustme

from pytest_httpserver import HTTPServer


def test_response_management():
    results1 = [_AccessResp("https://bad-cache/ns_path", False, PelicanException),
                _AccessResp("https://good-cache/ns_path", True),
                _AccessResp("https://good-cache/ns_path", True)]
    
    results2 = [_AccessResp("https://good-cache/ns_path", True),
                _AccessResp("https://good-cache/ns_path", True),
                _AccessResp("https://third-cache/ns_path", False, PelicanException)]

    aStats = _AccessStats()

    # Add a bad response
    ar_bad = _AccessResp("https://bad-cache/ns_path", False, PelicanException)
    aStats.add_response("ns_path", ar_bad)

    # Add a good response
    ar_good = _AccessResp("https://good-cache/ns_path", True)
    aStats.add_response("ns_path", ar_good)

    # Add a good response
    aStats.add_response("ns_path", ar_good)

    # Check results
    k, e = aStats.get_responses("ns_path")
    assert e
    assert str(k) == str(results1)

    # Add another response
    ar_new = _AccessResp("https://third-cache/ns_path", False, PelicanException)
    aStats.add_response("ns_path", ar_new)

    # Check that only the most recent three responses are available
    k, e = aStats.get_responses("ns_path")
    assert e
    assert len(k) == 3
    assert str(k) == str(results2)

    # Test no responses for path
    k, e = aStats.get_responses("no_path")
    assert e == False
    