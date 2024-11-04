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
import pelicanfs.core


def test_osdf():
    """
    Test using the OSDF protocol with fsspec
    """
    with fsspec.open("osdf:///ospool/uc-shared/public/OSG-Staff/validation/test.txt") as of:
        data = of.read()
    assert data == b'Hello, World!\n'


def test_osdf_pelicanurl():
    """
    Test using the Pelican protocol with fsspec
    """
    with fsspec\
        .open("pelican://osg-htc.org/ospool/uc-shared/public/OSG-Staff/validation/test.txt") as of:
        data = of.read()
    assert data == b'Hello, World!\n'


def test_osdf_direct():
    """
    Test using the Pelican protocol with the OSDF discovery url
    """
    pelfs = pelicanfs.core.PelicanFileSystem("pelican://osg-htc.org", direct_reads=True)
    data = pelfs.cat("/ospool/uc-shared/public/OSG-Staff/validation/test.txt")
    assert data == b'Hello, World!\n'
