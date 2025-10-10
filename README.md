# PelicanFS

[![DOI](https://zenodo.org/badge/751984532.svg)](https://zenodo.org/doi/10.5281/zenodo.13376216)

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Limitations](#limitations)
- [Installation](#installation)
- [Quick Start](#quick-start)
  - [Basic Usage](#basic-usage)
  - [Using the OSDF Scheme](#using-the-osdf-scheme)
- [Object Operations](#object-operations)
  - [Listing Objects and Collections](#listing-objects-and-collections)
  - [Pattern Matching with Glob](#pattern-matching-with-glob)
  - [Reading Objects](#reading-objects)
  - [Writing Objects](#writing-objects)
  - [Downloading Objects](#downloading-objects)
- [Advanced Configuration](#advanced-configuration)
  - [Specifying Endpoints](#specifying-endpoints)
  - [Enabling Direct Reads](#enabling-direct-reads)
  - [Specifying Preferred Caches](#specifying-preferred-caches)
- [Authorization](#authorization)
  - [1. Providing a Token via Headers](#1-providing-a-token-via-headers)
  - [2. Environment Variables](#2-environment-variables)
  - [3. Default Token Location](#3-default-token-location)
  - [4. HTCondor Token Discovery](#4-htcondor-token-discovery)
  - [Token File Formats](#token-file-formats)
  - [Automatic Token Discovery](#automatic-token-discovery)
  - [Token Scopes](#token-scopes)
  - [Token Validation](#token-validation)
- [Integration with Data Science Libraries](#integration-with-data-science-libraries)
  - [Using with xarray and Zarr](#using-with-xarray-and-zarr)
  - [Using with PyTorch](#using-with-pytorch)
  - [Using with Pandas](#using-with-pandas)
- [Getting an FSMap](#getting-an-fsmap)
- [Monitoring and Debugging](#monitoring-and-debugging)
  - [Access Statistics](#access-statistics)
  - [Enabling Debug Logging](#enabling-debug-logging)
- [API Reference](#api-reference)
  - [PelicanFileSystem](#pelicanfilesystem)
  - [OSDFFileSystem](#osdffilesystem)
  - [PelicanMap](#pelicanmap)
- [Examples](#examples)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)
- [Citation](#citation)
- [Support](#support)

## Overview

PelicanFS is a file system interface (fsspec) for the Pelican Platform. It provides a Python interface to interact with Pelican federations, allowing you to read, write, and manage objects across distributed object storage systems.

For more information about Pelican, see our [main website](https://pelicanplatform.org) or [GitHub page](https://github.com/PelicanPlatform/pelican). For more information about fsspec, visit the [filesystem-spec](https://filesystem-spec.readthedocs.io/en/latest/index.html) page.

For comprehensive tutorials and real-world examples using PelicanFS with geoscience datasets, see the [Project Pythia OSDF Cookbook](https://projectpythia.org/osdf-cookbook/).

**Note on Terminology:**
- In URL terminology, `pelican://` and `osdf://` are properly called **schemes**. While fsspec refers to these as "protocols," we use the term "scheme" throughout this documentation for technical accuracy.
- Pelican is an **object storage** system. Remote items are called **objects** (analogous to files) and **collections** (analogous to directories), not files and directories.

## Features

- **Read Operations**: List, read, and search for objects across Pelican namespaces
- **Write Operations**: Upload objects to Pelican origins with proper authorization
- **Smart Caching**: Automatic cache selection and fallback for optimal performance
- **Token Management**: Automatic token discovery and validation for authorized operations
- **Scheme Support**: Works with both `pelican://` and `osdf://` URL schemes
- **Integration**: Seamless integration with popular data science libraries (xarray, zarr, PyTorch, etc.)
- **Async Support**: Built on async foundations for efficient I/O operations

## Limitations

PelicanFS is built on top of the HTTP fsspec implementation. As such, any functionality that isn't available in the HTTP implementation is also *not* available in PelicanFS. Specifically:
- `rm` (remove files)
- `cp` (copy files)
- `mkdir` (create directories)
- `makedirs` (create directory trees)

These operations will raise `NotImplementedError` if called.

## Installation

To install PelicanFS from PyPI:

```bash
pip install pelicanfs
```

To install from source:

```bash
git clone https://github.com/PelicanPlatform/pelicanfs.git
cd pelicanfs
pip install -e .
```

## Quick Start

### Basic Usage

Create a `PelicanFileSystem` instance and provide it with your federation's discovery URL:

```python
from pelicanfs import PelicanFileSystem

# Connect to the OSDF federation
pelfs = PelicanFileSystem("pelican://osg-htc.org")

# List objects in a namespace
objects = pelfs.ls('/ospool/uc-shared/public/OSG-Staff/')

# Read an object
content = pelfs.cat('/ospool/uc-shared/public/OSG-Staff/validation/test.txt')
print(content)
```

### Using the OSDF Scheme

For convenience, you can use the `osdf://` scheme directly with fsspec or the `OSDFFileSystem` class:

```python
from pelicanfs.core import OSDFFileSystem
import fsspec

# Using OSDFFileSystem (automatically connects to osg-htc.org)
osdf = OSDFFileSystem()
objects = osdf.ls('/ospool/uc-shared/public/')

# Or use fsspec directly with the osdf:// scheme
with fsspec.open('osdf:///ospool/uc-shared/public/OSG-Staff/validation/test.txt', 'r') as f:
    content = f.read()
```

## Object Operations

### Listing Objects and Collections

```python
<<<<<<< HEAD
from pelicanfs import PelicanFileSystem, PelicanMap
=======
import fsspec
>>>>>>> a0d3a8e (Massive update to the pelicanfs README and documentation)

# Method 1: Using fsspec functions with schemes (recommended for most users)
objects = fsspec.ls('osdf:///ospool/uc-shared/public/')

# List with details (size, type, etc.)
objects_detailed = fsspec.ls('osdf:///ospool/uc-shared/public/', detail=True)

# Recursively find all objects
all_objects = fsspec.find('osdf:///ospool/uc-shared/public/')

# Find objects with depth limit
objects = fsspec.find('osdf:///ospool/uc-shared/public/', maxdepth=2)

# Method 2: Using PelicanFileSystem directly (for more control)
from pelicanfs.core import PelicanFileSystem
pelfs = PelicanFileSystem("pelican://osg-htc.org")
objects = pelfs.ls('/ospool/uc-shared/public/')
```

### Pattern Matching with Glob

```python
import fsspec

# Method 1: Using fsspec.glob with schemes (recommended)
csv_objects = fsspec.glob('osdf:///ospool/uc-shared/public/**/*.csv')

# Find objects with depth limit
json_objects = fsspec.glob('osdf:///ospool/uc-shared/public/**/*.json', maxdepth=3)

# Method 2: Using PelicanFileSystem directly
from pelicanfs.core import PelicanFileSystem
pelfs = PelicanFileSystem("pelican://osg-htc.org")
csv_objects = pelfs.glob('/ospool/uc-shared/public/**/*.csv')
```

### Reading Objects

```python
import fsspec

# Method 1: Using fsspec.open with schemes (recommended)
with fsspec.open('osdf:///ospool/uc-shared/public/OSG-Staff/validation/test.txt', 'r') as f:
    data = f.read()

# Using fsspec.cat to read entire object
content = fsspec.cat('osdf:///ospool/uc-shared/public/OSG-Staff/validation/test.txt')

# Read multiple objects
contents = fsspec.cat(['osdf:///ospool/uc-shared/public/file1.txt',
                       'osdf:///ospool/uc-shared/public/file2.txt'])

# Method 2: Using PelicanFileSystem directly (for more control)
from pelicanfs.core import PelicanFileSystem
pelfs = PelicanFileSystem("pelican://osg-htc.org")
content = pelfs.cat('/ospool/uc-shared/public/OSG-Staff/validation/test.txt')
```

### Writing Objects

To upload local files as objects, you need proper authorization (see [Authorization](#authorization) section):

```python
import fsspec

# Method 1: Using fsspec.put_file with schemes (recommended)
# Note: Pass storage_options for authorization
fsspec.put_file('/local/path/file.txt',
                'osdf:///namespace/remote/path/object.txt',
                storage_options={"headers": {"Authorization": "Bearer YOUR_TOKEN"}})

# Upload multiple local files as objects
fsspec.put('/local/directory/',
           'osdf:///namespace/remote/path/',
           recursive=True,
           storage_options={"headers": {"Authorization": "Bearer YOUR_TOKEN"}})

# Method 2: Using PelicanFileSystem directly
from pelicanfs.core import PelicanFileSystem
pelfs = PelicanFileSystem("pelican://osg-htc.org",
                          headers={"Authorization": "Bearer YOUR_TOKEN"})
pelfs.put('/local/path/file.txt', '/namespace/remote/path/object.txt')
```

### Downloading Objects

```python
import fsspec

# Method 1: Using fsspec.get with schemes (recommended)
# Download an object to a local file
fsspec.get('osdf:///ospool/uc-shared/public/object.txt', '/local/path/file.txt')

# Download multiple objects
fsspec.get('osdf:///ospool/uc-shared/public/', '/local/directory/', recursive=True)

# Method 2: Using PelicanFileSystem directly
from pelicanfs.core import PelicanFileSystem
pelfs = PelicanFileSystem("pelican://osg-htc.org")
pelfs.get('/ospool/uc-shared/public/object.txt', '/local/path/file.txt')
```

## Advanced Configuration

### Specifying Endpoints

PelicanFS allows you to control where data is read from, rather than letting the director automatically select the best cache.

**Note:** The `direct_reads` and `preferred_caches` settings are mutually exclusive. If `direct_reads=True`, data will always be read from origins and `preferred_caches` will be ignored. If `direct_reads=False` (the default), then `preferred_caches` will be used if specified.

#### Enabling Direct Reads

Read data directly from origins, bypassing caches entirely:

```python
pelfs = PelicanFileSystem("pelican://osg-htc.org", direct_reads=True)
```

This is useful when:
- You're close to the origin server
- You want to ensure you're reading the most up-to-date data
- Cache performance is poor

#### Specifying Preferred Caches

Specify one or more preferred caches to use:

```python
# Use a single preferred cache
pelfs = PelicanFileSystem(
    "pelican://osg-htc.org",
    preferred_caches=["https://cache.example.com"]
)

# Use multiple preferred caches with fallback to director's list
pelfs = PelicanFileSystem(
    "pelican://osg-htc.org",
    preferred_caches=[
        "https://cache1.example.com",
        "https://cache2.example.com",
        "+"  # Special value: append director's caches
    ]
)
```

The special cache value `"+"` indicates that the provided preferred caches should be prepended to the list of caches from the director.

## Authorization

PelicanFS supports token-based authorization for accessing protected namespaces and performing write operations. Tokens are used to verify that you have permission to perform operations on specific namespaces. Tokens can be provided in multiple ways, checked in the following order of precedence:

### 1. Providing a Token via Headers

You can explicitly provide an authorization token when creating the filesystem:

```python
pelfs = PelicanFileSystem(
    "pelican://osg-htc.org",
    headers={"Authorization": "Bearer YOUR_TOKEN_HERE"}
)
```

Or when using fsspec directly:

```python
import fsspec

with fsspec.open(
    'osdf:///namespace/path/file.txt',
    headers={"Authorization": "Bearer YOUR_TOKEN_HERE"}
) as f:
    data = f.read()
```

### 2. Environment Variables

PelicanFS will automatically discover tokens from several environment variables:

#### `BEARER_TOKEN` - Direct token value
```bash
export BEARER_TOKEN="your_token_here"
```

#### `BEARER_TOKEN_FILE` - Path to token file
```bash
export BEARER_TOKEN_FILE="/path/to/token/file"
```

#### `TOKEN` - Path to token file (legacy)
```bash
export TOKEN="/path/to/token/file"
```

### 3. Default Token Location

PelicanFS checks the default bearer token file location (typically `~/.config/htcondor/tokens.d/` or similar, depending on your system configuration).

### 4. HTCondor Token Discovery

For HTCondor environments, PelicanFS will automatically discover tokens from:
- `_CONDOR_CREDS` environment variable
- `.condor_creds` directory in the current working directory

### Token File Formats

Token files can be in two formats:

**Plain text token:**
```
eyJhbGciOiJFUzI1NiIsImtpZCI6InhyNzZwZzJyTmNVRFNrYXVWRmlDN2owbGxvbWU4NFpsdG44RGMxM0FHVWsiLCJ0eXAiOiJKV1QifQ...
```

**JSON format:**
```json
{
  "access_token": "eyJhbGciOiJFUzI1NiIsImtpZCI6InhyNzZwZzJyTmNVRFNrYXVWRmlDN2owbGxvbWU4NFpsdG44RGMxM0FHVWsiLCJ0eXAiOiJKV1QifQ...",
  "expires_in": 3600
}
```

PelicanFS will automatically extract the `access_token` field from JSON-formatted token files.

### Automatic Token Discovery


When you attempt an operation that requires authorization, PelicanFS will:

1. Check if the namespace requires a token (via the director response)
2. Search for existing tokens using the discovery methods above (in order of precedence)
3. Validate each discovered token to ensure it:
   - Has not expired
   - Has the correct issuer (matches the namespace's allowed issuers)
   - Has the necessary scopes for the requested operation
   - Is authorized for the specific namespace path
4. Use the first valid token found
5. Cache the validated token for subsequent operations

This happens transparently without requiring manual token management. If no valid token is found, the operation will fail with a `NoCredentialsException`.

**To use authenticated namespaces, you must obtain a valid token from your Pelican federation administrator or token issuer and make it available through one of the discovery methods above.**

### Token Scopes

PelicanFS validates that discovered tokens have the appropriate scopes for the requested operation:
- **Read operations** (`cat`, `open`, `ls`, `glob`, `find`): Require `storage.read` scope
- **Write operations** (`put`): Require `storage.create` scope

When obtaining tokens from your federation administrator or token issuer, ensure they include the necessary scopes for your intended operations.

### Token Validation

PelicanFS automatically validates tokens to ensure they:
- Have not expired
- Have the correct audience and issuer
- Have the necessary scopes for the requested operation
- Are authorized for the specific namespace path

## Integration with Data Science Libraries

PelicanFS integrates seamlessly with popular Python data science libraries.

### Using with xarray and Zarr

PelicanFS works great with xarray for reading Zarr datasets:

```python
import xarray as xr

# Method 1: Using the scheme directly (recommended - simplest)
ds = xr.open_dataset('osdf:///ospool/uc-shared/public/dataset.zarr', engine='zarr')

# Method 2: Using PelicanMap (useful for multiple datasets or custom configurations)
from pelicanfs.core import PelicanFileSystem, PelicanMap
pelfs = PelicanFileSystem("pelican://osg-htc.org")
zarr_store = PelicanMap('/ospool/uc-shared/public/dataset.zarr', pelfs=pelfs)
ds = xr.open_dataset(zarr_store, engine='zarr')

# Method 3: Opening multiple datasets with PelicanMap
file1 = PelicanMap("/ospool/uc-shared/public/file1.zarr", pelfs=pelfs)
file2 = PelicanMap("/ospool/uc-shared/public/file2.zarr", pelfs=pelfs)
ds = xr.open_mfdataset([file1, file2], engine='zarr')
```

### Using with PyTorch

PelicanFS can be used to load training data for PyTorch:

```python
import torch
from torch.utils.data import Dataset
import fsspec

class PelicanDataset(Dataset):
    def __init__(self, file_paths):
        self.file_paths = file_paths

    def __len__(self):
        return len(self.file_paths)

    def __getitem__(self, idx):
        # Read file using fsspec
        data = fsspec.cat(self.file_paths[idx])
        # Process your data here
        return data

# Method 1: Using fsspec with schemes (recommended)
files = fsspec.glob('osdf:///ospool/uc-shared/public/training/data/**/*.bin')
dataset = PelicanDataset(files)
dataloader = torch.utils.data.DataLoader(dataset, batch_size=32)

# Method 2: Using PelicanFileSystem directly (for more control)
from pelicanfs.core import PelicanFileSystem
pelfs = PelicanFileSystem("pelican://osg-htc.org")
files = pelfs.glob('/ospool/uc-shared/public/training/data/**/*.bin')
# Add scheme prefix to paths for fsspec.cat
files_with_scheme = [f'pelican://osg-htc.org{f}' for f in files]
dataset = PelicanDataset(files_with_scheme)
dataloader = torch.utils.data.DataLoader(dataset, batch_size=32)
```

### Using with Pandas

Read CSV and other tabular data formats:

```python
import pandas as pd
import fsspec

# Method 1: Using fsspec.open with schemes (recommended)
with fsspec.open('osdf:///ospool/uc-shared/public/data.csv', 'r') as f:
    df = pd.read_csv(f)

# Or read directly with pandas (if pandas supports the scheme)
df = pd.read_csv('osdf:///ospool/uc-shared/public/data.csv')

# Method 2: Using PelicanFileSystem directly
from pelicanfs.core import PelicanFileSystem
pelfs = PelicanFileSystem("pelican://osg-htc.org")
with pelfs.open('/ospool/uc-shared/public/data.csv', 'r') as f:
    df = pd.read_csv(f)
```

## Getting an FSMap

Some systems prefer a key-value mapper interface rather than a URL. Use `PelicanMap` for this:

```python
from pelicanfs.core import PelicanFileSystem, PelicanMap

pelfs = PelicanFileSystem("pelican://osg-htc.org")
mapper = PelicanMap("/namespace/path/dataset.zarr", pelfs=pelfs)

# Use with xarray
import xarray as xr
ds = xr.open_dataset(mapper, engine='zarr')
```

**Note:** Use `PelicanMap` instead of fsspec's `get_mapper()` for better compatibility with Pelican's architecture.

## Monitoring and Debugging

### Access Statistics

PelicanFS tracks cache access statistics to help diagnose performance issues. For each namespace path, it keeps the last three cache access attempts:

```python
from pelicanfs.core import PelicanFileSystem

pelfs = PelicanFileSystem("pelican://osg-htc.org")

# Perform some operations
pelfs.cat('/ospool/uc-shared/public/data.txt')
pelfs.cat('/ospool/uc-shared/public/data.txt')  # Second access
pelfs.cat('/ospool/uc-shared/public/data.txt')  # Third access

# Get access statistics object
stats = pelfs.get_access_data()

# Get responses for a specific path
responses, has_data = stats.get_responses('/ospool/uc-shared/public/data.txt')

if has_data:
    for resp in responses:
        print(resp)

# Print all statistics in a readable format
stats.print()
```

**Example output:**

```
{NamespacePath: https://cache1.example.com/ospool/uc-shared/public/data.txt, Success: True}
{NamespacePath: https://cache1.example.com/ospool/uc-shared/public/data.txt, Success: True}
{NamespacePath: https://cache2.example.com/ospool/uc-shared/public/data.txt, Success: False, Error: <class 'aiohttp.client_exceptions.ClientConnectorError'>}
/ospool/uc-shared/public/data.txt: {NamespacePath: https://cache1.example.com/ospool/uc-shared/public/data.txt, Success: True} {NamespacePath: https://cache1.example.com/ospool/uc-shared/public/data.txt, Success: True} {NamespacePath: https://cache2.example.com/ospool/uc-shared/public/data.txt, Success: False, Error: <class 'aiohttp.client_exceptions.ClientConnectorError'>}
```

**What the statistics show:**
- **NamespacePath**: The full cache URL that was accessed
- **Success**: Whether the cache access succeeded (`True`) or failed (`False`)
- **Error**: The exception type if the access failed (only shown on failures)

This helps identify:
- Which caches are being used for your requests
- Cache reliability and failure patterns
- Whether cache fallback is working correctly

### Enabling Debug Logging

Enable detailed logging to troubleshoot issues:

```python
import logging

# Set logging level for PelicanFS
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("fsspec.pelican")
logger.setLevel(logging.DEBUG)
```

## API Reference

### PelicanFileSystem

Main class for interacting with Pelican federations.

#### Constructor Parameters

- `federation_discovery_url` (str): The Pelican federation discovery URL (e.g., `"pelican://osg-htc.org"`)
- `direct_reads` (bool, optional): If `True`, read directly from origins instead of caches. Default: `False`
- `preferred_caches` (list, optional): List of preferred cache URLs. Use `"+"` to append director's caches
- `headers` (dict, optional): HTTP headers to include in requests. Use for authorization: `{"Authorization": "Bearer TOKEN"}`
- `use_listings_cache` (bool, optional): Enable caching of directory listings. Default: `False`
- `asynchronous` (bool, optional): Use async mode. Default: `False`
- `**kwargs`: Additional arguments passed to the underlying HTTP filesystem

#### Methods

##### Object Operations

- `ls(path, detail=True, **kwargs)` - List objects in a collection
- `cat(path, recursive=False, on_error="raise", **kwargs)` - Read object contents
- `open(path, mode, **kwargs)` - Open an object for reading or writing
- `glob(path, maxdepth=None, **kwargs)` - Find objects matching a pattern
- `find(path, maxdepth=None, withdirs=False, **kwargs)` - Recursively list all objects
- `put(lpath, rpath, recursive=False, **kwargs)` - Upload local file(s) as remote object(s)
- `get(rpath, lpath, recursive=False, **kwargs)` - Download remote object(s) to local file(s)

##### Utility Methods

- `get_access_data()` - Get cache access statistics
- `info(path, **kwargs)` - Get detailed information about an object
- `exists(path, **kwargs)` - Check if a path exists
- `isfile(path, **kwargs)` - Check if a path is an object
- `isdir(path, **kwargs)` - Check if a path is a collection

### OSDFFileSystem

Convenience class that automatically connects to the OSDF federation (`osg-htc.org`).

```python
from pelicanfs.core import OSDFFileSystem

# Equivalent to PelicanFileSystem("pelican://osg-htc.org")
osdf = OSDFFileSystem()
```

### PelicanMap

Create a filesystem mapper for use with libraries like xarray.

```python
PelicanMap(root, pelfs, check=False, create=False)
```

**Parameters:**
- `root` (str): The root path in the Pelican namespace
- `pelfs` (PelicanFileSystem): An initialized PelicanFileSystem instance
- `check` (bool, optional): Check if the path exists. Default: `False`
- `create` (bool, optional): Create the path if it doesn't exist. Default: `False`

## Examples

### Repository Examples

See the `examples/` directory for complete working examples:

- `examples/pelicanfs_example.ipynb` - Basic PelicanFS usage
- `examples/pytorch/` - Using PelicanFS with PyTorch for machine learning
- `examples/xarray/` - Using PelicanFS with xarray for scientific data
- `examples/intake/` - Using PelicanFS with Intake catalogs

### Project Pythia OSDF Cookbook

For comprehensive tutorials and real-world geoscience examples, see the [Project Pythia OSDF Cookbook](https://projectpythia.org/osdf-cookbook/), which includes:

- **NCAR GDEX datasets**: Meteorological, atmospheric composition, and oceanographic observations
- **FIU Envistor**: Climate datasets from south Florida
- **NOAA SONAR data**: Fisheries datasets in Zarr format
- **AWS OpenData**: Sentinel-2 satellite imagery
- **Interactive notebooks**: All examples are runnable in Binder or locally

The cookbook demonstrates streaming large scientific datasets using PelicanFS with tools like xarray, Dask, and more.

## Troubleshooting

### Common Issues

**Problem:** `NoAvailableSource` error when trying to access a file

**Solution:** This usually means no cache or origin is available for the namespace. Check:
- The namespace path is correct
- The federation URL is correct
- Network connectivity to the federation
- Try enabling `direct_reads=True` to bypass caches

**Problem:** `401 Unauthorized` or authorization errors

**Solution:**
- Ensure you've provided a valid token via the `headers` parameter
- Check that your token has the correct scopes for the operation
- Verify the token hasn't expired

**Problem:** Slow performance

**Solution:**
- Try specifying `preferred_caches` to use a cache closer to you
- Enable `use_listings_cache=True` if you're doing many directory listings
- Check cache access statistics with `get_access_data()` to identify problematic caches

**Problem:** `NotImplementedError` for certain operations

**Solution:** PelicanFS doesn't support `rm`, `cp`, `mkdir`, or `makedirs` operations as they're not available in the underlying HTTP filesystem. Use alternative approaches or the Pelican command-line tools.

## Contributing

Contributions are welcome! Please see our [GitHub repository](https://github.com/PelicanPlatform/pelicanfs) for:
- Reporting issues
- Submitting pull requests
- Development guidelines

## License

PelicanFS is licensed under the Apache License 2.0. See the LICENSE file for details.

## Citation

If you use PelicanFS in your research, please cite:

```bibtex
@software{pelicanfs,
  author = {Pelican Platform Team},
  title = {PelicanFS: A filesystem interface for the Pelican Platform},
  year = {2024},
  doi = {10.5281/zenodo.13376216},
  url = {https://github.com/PelicanPlatform/pelicanfs}
}
```

## Support

For questions, issues, or support:
- Open an issue on [GitHub](https://github.com/PelicanPlatform/pelicanfs/issues)
- Visit the [Pelican Platform website](https://pelicanplatform.org)
- Join our community discussions
