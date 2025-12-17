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

PelicanFS is a filesystem specification (fsspec) implementation for the Pelican Platform. It provides a Python interface to interact with Pelican federations, allowing you to read, write, and manage objects across distributed object storage systems.

For more information about Pelican, see our [main website](https://pelicanplatform.org), [documentation](https://docs.pelicanplatform.org), or [GitHub page](https://github.com/PelicanPlatform/pelican). For more information about fsspec, visit the [filesystem-spec](https://filesystem-spec.readthedocs.io/en/latest/index.html) page.

For comprehensive tutorials and real-world examples using PelicanFS with geoscience datasets, see the [Project Pythia OSDF Cookbook](https://projectpythia.org/osdf-cookbook/).

**Note on Terminology:**
- In URL terminology, `pelican://` and `osdf://` are properly called **schemes**. While fsspec refers to these as "protocols," we use the term "scheme" throughout this documentation for technical accuracy.
- Pelican works with **objects** (analogous to files) and **collections** (analogous to directories), not files and directories. Unlike traditional files, Pelican objects are immutable—once created, their content should not change without renaming, as cached copies won't automatically update. Objects also lack filesystem-specific metadata like permissions or modification timestamps. Collections are organized using namespace prefixes that function hierarchically, similar to directory structures. For more details, see the [Pelican core concepts documentation](https://docs.pelicanplatform.org/about-pelican/core-concepts).

## Features

- **Read Operations**: List, read, and search for objects across Pelican namespaces
- **Write Operations**: Upload objects to Pelican Origins with proper authorization
- **Smart Caching**: Automatic cache selection and fallback for optimal performance
- **Token Management**: Automatic token discovery and validation for authorized operations
- **Scheme Support**: Works with both `pelican://` and `osdf://` URL schemes
- **Integration**: Seamless integration with popular data science libraries (xarray, zarr, PyTorch, etc.)
- **Async Support**: Built on async foundations for efficient I/O operations

## Limitations

PelicanFS is built on top of the HTTP fsspec implementation. As such, any functionality that isn't available in the HTTP implementation is also *not* available in PelicanFS. Specifically:
- `rm` (remove objects)
- `cp` (copy objects within the federation - note that downloading objects via `get()` to local files works normally)
- `mkdir` (create collections)
- `makedirs` (create collection trees)

These operations will raise a `NotImplementedError` if called.

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
objects = pelfs.ls('/pelicanplatform/test/')
print(objects)

# Read an object
content = pelfs.cat('/pelicanplatform/test/hello-world.txt')
print(content)
```

### Using the OSDF Scheme

The Open Science Data Federation (OSDF) is a specific Pelican federation operated by the OSG Consortium. The `osdf://` scheme is a convenience shortcut that automatically connects to the OSDF federation at `osg-htc.org`, so you don't need to specify the discovery URL explicitly.

**OSDFFileSystem vs PelicanFileSystem:** `OSDFFileSystem` is similarly a convenience class that wraps `PelicanFileSystem` and automatically uses `osg-htc.org` as the discovery URL. Using `OSDFFileSystem()` is equivalent to `PelicanFileSystem("pelican://osg-htc.org")`. If you're specifically working with the OSDF federation, `OSDFFileSystem` saves you from having to specify the discovery URL. For other Pelican federations, use `PelicanFileSystem` with the appropriate discovery URL.

```python
from pelicanfs.core import OSDFFileSystem
import fsspec

# Using OSDFFileSystem (automatically connects to osg-htc.org)
osdf = OSDFFileSystem()
objects = osdf.ls('/pelicanplatform/test/')

# Or use fsspec directly with the osdf:// scheme
with fsspec.open('osdf:///pelicanplatform/test/hello-world.txt', 'r') as f:
    content = f.read()
    print(content)
```

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

## Object Operations

### Listing Objects and Collections

**Choosing an approach:** Method 1 (using fsspec.filesystem with schemes) is recommended for most users as it works with any fsspec-compatible code and is portable across different storage backends. Method 2 (using PelicanFileSystem directly) gives you more control when you need to reuse a filesystem instance across multiple operations or access PelicanFS-specific features like getting access statistics.

```python
from pelicanfs import PelicanFileSystem
import fsspec

# Method 1: Using fsspec.filesystem() with schemes (recommended)
fs = fsspec.filesystem('osdf')
objects = fs.ls('/pelicanplatform/test/')

# List with details (size, type, etc.)
objects_detailed = fs.ls('/pelicanplatform/test/', detail=True)

# Recursively find all objects
all_objects = fs.find('/pelicanplatform/test/')

# Find objects with depth limit
objects = fs.find('/pelicanplatform/test/', maxdepth=2)

# Method 2: Using PelicanFileSystem directly (for more control)
pelfs = PelicanFileSystem("pelican://osg-htc.org")
objects = pelfs.ls('/pelicanplatform/test/')
```

### Pattern Matching with Glob

> [!WARNING]
> Glob operations with `**` patterns can be expensive for large namespaces as they recursively search through all subdirectories. Consider using `maxdepth` to limit the search depth or more specific patterns to reduce the search space.

```python
import fsspec

# Method 1: Using fsspec.filesystem() with schemes (recommended)
fs = fsspec.filesystem('osdf')

# Find all text files in the namespace
txt_objects = fs.glob('/pelicanplatform/**/*.txt')

# Find objects with depth limit
objects = fs.glob('/pelicanplatform/**/*', maxdepth=2)

# Method 2: Using PelicanFileSystem directly
from pelicanfs.core import PelicanFileSystem
pelfs = PelicanFileSystem("pelican://osg-htc.org")
txt_objects = pelfs.glob('/pelicanplatform/**/*.txt')
```

### Reading Objects

```python
import fsspec

# Method 1: Using fsspec.open with schemes (recommended)
with fsspec.open('osdf:///pelicanplatform/test/hello-world.txt', 'r') as f:
    data = f.read()
    print(data)

# Method 2: Using fsspec.filesystem() for cat operations
fs = fsspec.filesystem('osdf')

# Read entire object
content = fs.cat('/pelicanplatform/test/hello-world.txt')
print(content)

# Read multiple objects
contents = fs.cat(['/pelicanplatform/test/hello-world.txt',
                   '/pelicanplatform/test/testfile-64M'])

# Method 3: Using PelicanFileSystem directly (for more control)
from pelicanfs.core import PelicanFileSystem
pelfs = PelicanFileSystem("pelican://osg-htc.org")
content = pelfs.cat('/pelicanplatform/test/hello-world.txt')
print(content)
```

### Writing Objects

To upload local files as objects, you need proper authorization (see [Authorization](#authorization) section):

```python
# Note: Replace placeholder paths with your actual file paths, namespace, and token
import fsspec

# Method 1: Using fsspec.filesystem() with authorization (recommended)
fs = fsspec.filesystem('osdf', headers={"Authorization": "Bearer YOUR_TOKEN"})

# Upload a single file
fs.put('/local/path/file.txt', '/namespace/remote/path/object.txt')

# Upload multiple files
fs.put('/local/directory/', '/namespace/remote/path/', recursive=True)

# Method 2: Using PelicanFileSystem directly (for more control)
from pelicanfs.core import PelicanFileSystem
pelfs = PelicanFileSystem("pelican://osg-htc.org",
                          headers={"Authorization": "Bearer YOUR_TOKEN"})
pelfs.put('/local/path/file.txt', '/namespace/remote/path/object.txt')
```

### Downloading Objects

**Reading vs Downloading:** Reading objects (via `cat()`, `open()`) loads data into memory for processing within your Python program. Downloading objects (via `get()`) saves them as files on your local filesystem. Use `get()` when you need persistent local copies; use reading operations for direct data processing.

```python
# Note: Replace '/local/path/' and '/local/directory/' with your actual local destination paths
import fsspec

# Method 1: Using fsspec.filesystem() (recommended)
fs = fsspec.filesystem('osdf')

# Download an object to a local file
fs.get('/pelicanplatform/test/hello-world.txt', '/local/path/file.txt')

# Download multiple objects (note: no trailing slash on source path)
fs.get('/pelicanplatform/test', '/local/directory/', recursive=True)

# Method 2: Using PelicanFileSystem directly
from pelicanfs.core import PelicanFileSystem
pelfs = PelicanFileSystem("pelican://osg-htc.org")
pelfs.get('/pelicanplatform/test/hello-world.txt', '/local/path/file.txt')
```

## Advanced Configuration

### Specifying Endpoints

PelicanFS allows you to control where data is read from, rather than letting the Director automatically select the best Cache.

**Note:** The `direct_reads` and `preferred_caches` settings are mutually exclusive. If `direct_reads=True`, data will always be read from Origins and `preferred_caches` will be ignored. If `direct_reads=False` (the default), then `preferred_caches` will be used if specified.

#### Enabling Direct Reads

Read data directly from Origins, bypassing Caches entirely:

```python
pelfs = PelicanFileSystem("pelican://osg-htc.org", direct_reads=True)
```

This is useful when:
- You're physically close to the Origin server (better network latency)
- Cache performance is poor
- Your workflows don't benefit from object caching/reuse

#### Specifying Preferred Caches

Specify one or more preferred caches to use:

```python
# Note: Replace example cache URLs with actual Cache server URLs from your federation
# Use a single preferred cache
pelfs = PelicanFileSystem(
    "pelican://osg-htc.org",
    preferred_caches=["https://cache.example.com"]
)

# Use multiple preferred caches with fallback to Director's list
pelfs = PelicanFileSystem(
    "pelican://osg-htc.org",
    preferred_caches=[
        "https://cache1.example.com",
        "https://cache2.example.com",
        "+"  # Special value: append Director's caches
    ]
)
```

**Important:** If you specify `preferred_caches` without the `"+"` value, PelicanFS will **only** attempt to use your specified Caches and will not fall back to the Director's Cache list. This means if all your preferred Caches fail, the operation will fail rather than trying other available Caches. The Director has knowledge about Cache health, load, and availability—ignoring its recommendations means you lose these benefits.

The special Cache value `"+"` indicates that your preferred Caches should be tried first, followed by the Director's recommended Caches as a fallback.

## Authorization

PelicanFS supports token-based authorization for accessing protected namespaces and performing write operations. Tokens are used to verify that you have permission to perform operations on specific namespaces.

**To use authenticated namespaces, you must obtain a valid token from your Pelican federation administrator or token issuer and make it available through one of the discovery methods below.**

Tokens can be provided in multiple ways, checked in the following order of precedence:

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

1. Check if the namespace requires a token (via the Director response)
2. Search for existing tokens using the discovery methods above (in order of precedence)
3. Validate each discovered token to ensure it:
   - Has not expired
   - Has the correct issuer (matches the namespace's allowed issuers)
   - Has the necessary scopes for the requested operation
   - Is authorized for the specific namespace path
4. Use the first valid token found
5. Cache the validated token for subsequent operations

This happens transparently without requiring manual token management. If no valid token is found, the operation will fail with a `NoCredentialsException`.

### Token Scopes

PelicanFS validates that discovered tokens have the appropriate scopes for the requested operation. Pelican supports both WLCG and SciTokens2 scope formats:
- **Read operations** (`cat`, `open`, `ls`, `glob`, `find`): Require `storage.read:<path>` (WLCG) or `read:<path>` (SciTokens2) scope
- **Write operations** (`put`): Require `storage.create:<path>` (WLCG) or `write:<path>` (SciTokens2) scope

When obtaining tokens from your federation administrator or token issuer, ensure they include the necessary scopes for your intended operations.

### Token Validation

PelicanFS automatically validates tokens to ensure they:
- Have not expired
- Have the correct audience and issuer
- Have the necessary scopes for the requested operation
- Are authorized for the specific namespace path

## Integration with Data Science Libraries

PelicanFS integrates with any Python library that supports FFSpec.

### Using with xarray and Zarr

PelicanFS works with xarray for reading Zarr datasets:

```python
# Note: Replace example paths with actual Zarr dataset paths in your namespace
import xarray as xr

# Method 1: Using the scheme directly (recommended - simplest)
ds = xr.open_dataset('osdf:///namespace/remote/path/dataset.zarr', engine='zarr')

# Method 2: Using PelicanMap (useful for multiple datasets or custom configurations)
from pelicanfs.core import PelicanFileSystem, PelicanMap
pelfs = PelicanFileSystem("pelican://osg-htc.org")
zarr_store = PelicanMap('/namespace/remote/path/dataset.zarr', pelfs=pelfs)
ds = xr.open_dataset(zarr_store, engine='zarr')

# Method 3: Opening multiple datasets with PelicanMap
file1 = PelicanMap("/namespace/remote/path/file1.zarr", pelfs=pelfs)
file2 = PelicanMap("/namespace/remote/path/file2.zarr", pelfs=pelfs)
ds = xr.open_mfdataset([file1, file2], engine='zarr')
```

### Using with PyTorch

PelicanFS can be used to load training data for PyTorch:

```python
# Note: Replace example paths with actual training data paths in your namespace
import torch
from torch.utils.data import Dataset
import fsspec

class PelicanDataset(Dataset):
    def __init__(self, file_paths, fs):
        self.file_paths = file_paths
        self.fs = fs

    def __len__(self):
        return len(self.file_paths)

    def __getitem__(self, idx):
        # Read file using filesystem instance
        data = self.fs.cat(self.file_paths[idx])
        # Process your data here
        return data

# Method 1: Using fsspec.filesystem() (recommended)
fs = fsspec.filesystem('osdf')
files = fs.glob('/namespace/remote/path/**/*.bin')
dataset = PelicanDataset(files, fs)
dataloader = torch.utils.data.DataLoader(dataset, batch_size=32)

# Method 2: Using PelicanFileSystem directly (for more control)
from pelicanfs.core import PelicanFileSystem
pelfs = PelicanFileSystem("pelican://osg-htc.org")
files = pelfs.glob('/namespace/remote/path/**/*.bin')
dataset = PelicanDataset(files, pelfs)
dataloader = torch.utils.data.DataLoader(dataset, batch_size=32)
```

### Using with Pandas

Read CSV and other tabular data formats:

```python
# Note: Replace example path with your actual CSV file path
import pandas as pd
import fsspec

# Method 1: Using fsspec.open with schemes (recommended)
with fsspec.open('osdf:///namespace/remote/path/data.csv', 'r') as f:
    df = pd.read_csv(f)

# Method 2: Read directly with pandas (pandas will use fsspec internally)
df = pd.read_csv('osdf:///namespace/remote/path/data.csv')

# Method 3: Using PelicanFileSystem directly
from pelicanfs.core import PelicanFileSystem
pelfs = PelicanFileSystem("pelican://osg-htc.org")
with pelfs.open('/namespace/remote/path/data.csv', 'r') as f:
    df = pd.read_csv(f)
```

## Getting an FSMap

Some systems prefer a key-value mapper interface rather than a URL. Use `PelicanMap` for this:

```python
# Note: Replace example path with your actual dataset path
from pelicanfs.core import PelicanFileSystem, PelicanMap

pelfs = PelicanFileSystem("pelican://osg-htc.org")
mapper = PelicanMap("/namespace/remote/path/dataset.zarr", pelfs=pelfs)

# Use with xarray
import xarray as xr
ds = xr.open_dataset(mapper, engine='zarr')
```

**Note:** Use `PelicanMap` instead of fsspec's `get_mapper()` for better compatibility with Pelican's architecture.

## Monitoring and Debugging

### Access Statistics

PelicanFS tracks Cache access statistics to help diagnose performance issues. For each namespace path, it keeps the last three Cache access attempts.

**What the statistics show:**
- **NamespacePath**: The full Cache URL that was accessed
- **Success**: Whether the Cache access succeeded (`True`) or failed (`False`)
- **Error**: The exception type if the access failed (only shown on failures)

This helps identify:
- Which Caches are being used for your requests
- Cache reliability and failure patterns
- Whether Cache fallback is working correctly

**Example usage:**

```python
from pelicanfs.core import PelicanFileSystem

pelfs = PelicanFileSystem("pelican://osg-htc.org")

# Perform some operations
pelfs.cat('/pelicanplatform/test/hello-world.txt')
pelfs.cat('/pelicanplatform/test/hello-world.txt')  # Second access
pelfs.cat('/pelicanplatform/test/hello-world.txt')  # Third access

# Get access statistics object
stats = pelfs.get_access_data()

# Get responses for a specific path
responses, has_data = stats.get_responses('/pelicanplatform/test/hello-world.txt')

if has_data:
    for resp in responses:
        print(resp)

# Print all statistics in a readable format
stats.print()
```

**Example output:**

```
{NamespacePath: https://cache1.example.com/pelicanplatform/test/hello-world.txt, Success: True}
{NamespacePath: https://cache1.example.com/pelicanplatform/test/hello-world.txt, Success: True}
{NamespacePath: https://cache2.example.com/pelicanplatform/test/hello-world.txt, Success: False, Error: <class 'aiohttp.client_exceptions.ClientConnectorError'>}
/pelicanplatform/test/hello-world.txt: {NamespacePath: https://cache1.example.com/pelicanplatform/test/hello-world.txt, Success: True} {NamespacePath: https://cache1.example.com/pelicanplatform/test/hello-world.txt, Success: True} {NamespacePath: https://cache2.example.com/pelicanplatform/test/hello-world.txt, Success: False, Error: <class 'aiohttp.client_exceptions.ClientConnectorError'>}
```

### Enabling Debug Logging

Enable detailed logging to troubleshoot issues:

```python
import logging

# Set logging level for PelicanFS
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("fsspec.pelican")
logger.setLevel(logging.DEBUG)
```

**Logging levels and what they show:**
- `DEBUG`: Detailed information including cache URLs being tried, token discovery, Director responses, and all HTTP requests
- `INFO`: High-level operations like file opens, reads, and writes
- `WARNING`: Issues that don't prevent operation but may indicate problems (e.g., falling back to alternate caches)
- `ERROR`: Operation failures and exceptions

## API Reference

### PelicanFileSystem

Main class for interacting with Pelican federations.

#### Constructor Parameters

- `federation_discovery_url` (str): The Pelican federation discovery URL (e.g., `"pelican://osg-htc.org"`)
- `direct_reads` (bool, optional): If `True`, read directly from Origins instead of Caches. Default: `False`
- `preferred_caches` (list, optional): List of preferred Cache URLs. Use `"+"` to append Director's Caches
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

- `get_access_data()` - Get Cache access statistics
- `info(path, **kwargs)` - Get detailed information about an object
- `exists(path, **kwargs)` - Check if a path exists
- `isfile(path, **kwargs)` - Check if a path is an object
- `isdir(path, **kwargs)` - Check if a path is a collection

### OSDFFileSystem

Convenience class that automatically connects to the OSDF federation (which uses `osg-htc.org` for its discovery URL).

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
- `root` (str): The namespace path within Pelican to use as the base of this mapper (e.g., `/namespace/path/dataset.zarr`). This acts like a mount point - paths within the mapper are relative to this base path.
- `pelfs` (PelicanFileSystem): An initialized PelicanFileSystem instance
- `check` (bool, optional): Check if the path exists. Default: `False`
- `create` (bool, optional): Inherited from fsspec's FSMap but not functional in PelicanFS (operations like `mkdir` are not supported). Default: `False`

## Troubleshooting

### Common Issues

**Problem:** `NoAvailableSource` error when trying to access a file

**Solution:** This usually means no Cache or Origin is available for the namespace. Check:
- The namespace path is correct
- The federation URL is correct
- Network connectivity to the federation
- Try enabling `direct_reads=True` to bypass Caches

**Problem:** `403 Forbidden` or authorization errors

**Solution:**
- Ensure you've provided a valid token via the `headers` parameter or one of the other token discovery methods (see [Authorization](#authorization))
- Verify the token hasn't expired

**Problem:** Slow performance

**Solution:**
- Enable `use_listings_cache=True` if you're doing many directory listings

**Problem:** `NotImplementedError` for certain operations

**Solution:** PelicanFS doesn't support `rm`, `cp`, `mkdir`, or `makedirs` operations as they're not available in the underlying HTTP filesystem. Use alternative approaches or the Pelican command-line tools.

## Contributing

Contributions are welcome! Please see our [GitHub repository](https://github.com/PelicanPlatform/pelicanfs) for reporting issues and submitting pull requests.

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
- Join our [community discussions](https://github.com/PelicanPlatform/pelican/discussions)
- Visit the [Pelican Platform website](https://pelicanplatform.org)
