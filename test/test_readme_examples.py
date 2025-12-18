"""
Test file to verify executable examples from README.md work correctly.
This file is not meant for upload - it's for validation purposes only.

Note: This only tests examples that use real, publicly accessible paths.
Examples with placeholder paths (like /namespace/remote/path/) are not tested.
"""
import os
import tempfile

import fsspec
import pytest

from pelicanfs import OSDFFileSystem, PelicanFileSystem


class TestQuickStartExamples:
    """Test examples from Quick Start section"""

    def test_basic_usage(self):
        """Test basic usage example"""
        # Connect to the OSDF federation
        pelfs = PelicanFileSystem("pelican://osg-htc.org")

        # List objects in a namespace
        objects = pelfs.ls("/pelicanplatform/test/")
        print(objects)
        assert isinstance(objects, list)
        assert len(objects) > 0

        # Read an object
        content = pelfs.cat("/pelicanplatform/test/hello-world.txt")
        print(content)
        assert content is not None

    def test_osdf_scheme(self):
        """Test OSDF scheme examples"""
        # Using OSDFFileSystem (automatically connects to osg-htc.org)
        osdf = OSDFFileSystem()
        objects = osdf.ls("/pelicanplatform/test/")
        assert isinstance(objects, list)
        assert len(objects) > 0

        # Or use fsspec directly with the osdf:// scheme
        with fsspec.open("osdf:///pelicanplatform/test/hello-world.txt", "r") as f:
            content = f.read()
            print(content)
            assert content is not None


class TestObjectOperations:
    """Test examples from Object Operations section"""

    def test_listing_objects(self):
        """Test listing objects and collections"""
        # Method 1: Using fsspec.filesystem() with schemes (recommended - works with any fsspec-compatible code)
        fs = fsspec.filesystem("osdf")
        objects = fs.ls("/pelicanplatform/test/")
        assert isinstance(objects, list)

        # List with details (size, type, etc.)
        objects_detailed = fs.ls("/pelicanplatform/test/", detail=True)
        assert isinstance(objects_detailed, list)

        # Recursively find all objects
        all_objects = fs.find("/pelicanplatform/test/")
        assert isinstance(all_objects, (list, dict))

        # Find objects with depth limit
        objects = fs.find("/pelicanplatform/test/", maxdepth=2)
        assert isinstance(objects, (list, dict))

        # Method 2: Using PelicanFileSystem directly (for more control)
        pelfs = PelicanFileSystem("pelican://osg-htc.org")
        objects = pelfs.ls("/pelicanplatform/test/")
        assert isinstance(objects, list)

    def test_glob_operations(self):
        """Test pattern matching with glob"""
        # Method 1: Using fsspec.filesystem() with schemes (recommended)
        fs = fsspec.filesystem("osdf")

        # Find all text files in the namespace
        txt_objects = fs.glob("/pelicanplatform/**/*.txt")
        assert isinstance(txt_objects, list)

        # Find objects with depth limit
        objects = fs.glob("/pelicanplatform/**/*", maxdepth=2)
        assert isinstance(objects, list)

        # Method 2: Using PelicanFileSystem directly
        from pelicanfs.core import PelicanFileSystem

        pelfs = PelicanFileSystem("pelican://osg-htc.org")
        txt_objects = pelfs.glob("/pelicanplatform/**/*.txt")
        assert isinstance(txt_objects, list)

    def test_reading_objects(self):
        """Test reading objects"""
        # Method 1: Using fsspec.open with schemes (recommended - works with any fsspec-compatible code)
        with fsspec.open("osdf:///pelicanplatform/test/hello-world.txt", "r") as f:
            data = f.read()
            print(data)
            assert data is not None

        # Method 2: Using fsspec.filesystem() for cat operations
        fs = fsspec.filesystem("osdf")

        # Read entire object
        content = fs.cat("/pelicanplatform/test/hello-world.txt")
        print(content)
        assert content is not None

        # Read multiple objects
        contents = fs.cat(["/pelicanplatform/test/hello-world.txt", "/pelicanplatform/test/testfile-64M"])
        assert isinstance(contents, dict)
        assert len(contents) == 2

        # Method 3: Using PelicanFileSystem directly (for more control)
        from pelicanfs.core import PelicanFileSystem

        pelfs = PelicanFileSystem("pelican://osg-htc.org")
        content = pelfs.cat("/pelicanplatform/test/hello-world.txt")
        print(content)
        assert content is not None

    def test_downloading_objects(self):
        """Test downloading objects"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Method 1: Using fsspec.filesystem() (recommended - works with any fsspec-compatible code)
            fs = fsspec.filesystem("osdf")

            # Download an object to a local file
            local_file = os.path.join(tmpdir, "file.txt")
            fs.get("/pelicanplatform/test/hello-world.txt", local_file)
            assert os.path.exists(local_file)

            # Verify content was downloaded
            with open(local_file, "r") as f:
                content = f.read()
                assert len(content) > 0
                print(f"Downloaded file content: {content[:100]}")

            # Download multiple objects
            local_dir = os.path.join(tmpdir, "test_files")
            os.makedirs(local_dir, exist_ok=True)
            fs.get("/pelicanplatform/test", local_dir, recursive=True)
            assert os.path.exists(local_dir)

            # Check that files were downloaded
            downloaded_files = os.listdir(local_dir)
            assert len(downloaded_files) > 0
            print(f"Downloaded {len(downloaded_files)} files")

            # Method 2: Using PelicanFileSystem directly
            from pelicanfs.core import PelicanFileSystem

            pelfs = PelicanFileSystem("pelican://osg-htc.org")
            local_file2 = os.path.join(tmpdir, "file2.txt")
            pelfs.get("/pelicanplatform/test/hello-world.txt", local_file2)
            assert os.path.exists(local_file2)

            # Verify content was downloaded
            with open(local_file2, "r") as f:
                content = f.read()
                assert len(content) > 0


class TestAdvancedConfiguration:
    """Test examples from Advanced Configuration section"""

    def test_direct_reads(self):
        """Test enabling direct reads"""
        pelfs = PelicanFileSystem("pelican://osg-htc.org", direct_reads=True)
        # Just verify it initializes correctly
        assert pelfs is not None

        # Try a read operation
        content = pelfs.cat("/pelicanplatform/test/hello-world.txt")
        assert content is not None


class TestMonitoringDebugging:
    """Test examples from Monitoring and Debugging section"""

    def test_access_statistics(self):
        """Test access statistics"""
        from pelicanfs.core import PelicanFileSystem

        pelfs = PelicanFileSystem("pelican://osg-htc.org")

        # Perform some operations
        pelfs.cat("/pelicanplatform/test/hello-world.txt")
        pelfs.cat("/pelicanplatform/test/hello-world.txt")  # Second access
        pelfs.cat("/pelicanplatform/test/hello-world.txt")  # Third access

        # Get access statistics object
        stats = pelfs.get_access_data()
        assert stats is not None

        # Get responses for a specific path
        responses, has_data = stats.get_responses("/pelicanplatform/test/hello-world.txt")

        if has_data:
            for resp in responses:
                print(resp)
                assert resp is not None

        # Print all statistics in a readable format
        stats.print()

    def test_debug_logging(self):
        """Test enabling debug logging"""
        import logging

        # Set logging level for PelicanFS
        logging.basicConfig(level=logging.DEBUG)
        logger = logging.getLogger("fsspec.pelican")
        logger.setLevel(logging.DEBUG)

        # Verify logger is configured
        assert logger.level == logging.DEBUG


class TestAPIReference:
    """Test examples from API Reference section"""

    def test_osdf_filesystem(self):
        """Test OSDFFileSystem example"""
        from pelicanfs.core import OSDFFileSystem

        # Equivalent to PelicanFileSystem("pelican://osg-htc.org")
        osdf = OSDFFileSystem()
        assert osdf is not None

        # Verify it works
        content = osdf.cat("/pelicanplatform/test/hello-world.txt")
        assert content is not None


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "-s"])
