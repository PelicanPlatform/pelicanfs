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
from pelicanfs.core import PelicanException, _AccessResp, _AccessStats


def test_response_management():
    """
    Testing the AccessStats and AccessResp
    """
    results1 = [
        _AccessResp("https://bad-cache/ns_path", False, PelicanException),
        _AccessResp("https://good-cache/ns_path", True),
        _AccessResp("https://good-cache/ns_path", True),
    ]

    results2 = [
        _AccessResp("https://good-cache/ns_path", True),
        _AccessResp("https://good-cache/ns_path", True),
        _AccessResp("https://third-cache/ns_path", False, PelicanException),
    ]

    a_stats = _AccessStats()

    # Add a bad response
    ar_bad = _AccessResp("https://bad-cache/ns_path", False, PelicanException)
    a_stats.add_response("ns_path", ar_bad)

    # Add a good response
    ar_good = _AccessResp("https://good-cache/ns_path", True)
    a_stats.add_response("ns_path", ar_good)

    # Add a good response
    a_stats.add_response("ns_path", ar_good)

    # Check results
    k, e = a_stats.get_responses("ns_path")
    assert e
    assert str(k) == str(results1)

    # Add another response
    ar_new = _AccessResp("https://third-cache/ns_path", False, PelicanException)
    a_stats.add_response("ns_path", ar_new)

    # Check that only the most recent three responses are available
    k, e = a_stats.get_responses("ns_path")
    assert e
    assert len(k) == 3
    assert str(k) == str(results2)

    # Test no responses for path
    k, e = a_stats.get_responses("no_path")
    assert e is False
