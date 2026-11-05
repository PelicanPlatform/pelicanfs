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
    with fsspec.open("osdf:///pelicanplatform/test/hello-world.txt") as of:
        data = of.read()
    assert data == b"If you are seeing this message, getting an object from OSDF was successful.\n"


def test_osdf_pelicanurl():
    with fsspec.open("pelican://osg-htc.org/pelicanplatform/test/hello-world.txt") as of:
        data = of.read()
    assert data == b"If you are seeing this message, getting an object from OSDF was successful.\n"


def test_osdf_direct():
    pelfs = pelicanfs.core.PelicanFileSystem("pelican://osg-htc.org", direct_reads=True)
    data = pelfs.cat("/pelicanplatform/test/hello-world.txt")
    assert data == b"If you are seeing this message, getting an object from OSDF was successful.\n"
