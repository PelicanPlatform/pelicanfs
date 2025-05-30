# PelicanFS

[![DOI](https://zenodo.org/badge/751984532.svg)](https://zenodo.org/doi/10.5281/zenodo.13376216)

## Overview

PelicanFS is a file system interface (fsspec) for the Pelican Platform.  For more information about pelican, see our [main website](https://pelicanplatform.org) or [Github page](https://github.com/PelicanPlatform/pelican). For more information about fsspec, visit the [filesystem-spec](https://filesystem-spec.readthedocs.io/en/latest/index.html) page.


## Limitations

PelicanFS is built on top of the http fsspec implementation. As such, any functionality that isn’t available in the http implementation is also *not* available in PelicanFS.

## Installation

To install pelican, run:

```
pip install pelicanfs
```

To install from source, run:

```
git clone https://github.com/PelicanPlatform/pelicanfs.git
cd pelicanfs
pip install -e .
```


## Using PelicanFS

To use pelicanfs, first create a `PelicanFileSystem` and provide it with the pelican federation url. As an example using the OSDF federation

```python
from pelicanfs.core import PelicanFileSystem

pelfs = PelicanFileSystem("pelican://osg-htc.org")
```

Once `pelfs` is pointed at your federation's director, fsspec commands can be applied to Pelican namespaces. For example:

```python
hello_world = pelfs.cat('/ospool/uc-shared/public/OSG-Staff/validation/test.txt')
print(hello_world)
```

### Getting an FSMap

Sometimes various systems that interact with an fsspec want a key-value mapper rather than a url. To do that, call the `PelicanMap` function with the namespace path and a `PelicanFileSystem` object rather than using the fsspec `get_mapper` call. For example:

```python
from pelicanfs.core import PelicanFileSystem, PelicanMap

pelfs = PelicanFileSystem("some-director-url")
file1 = PelicanMap("/namespace/file/1", pelfs=pelfs)
file2 = PelicanMap("/namespace/file/2", pelfs=pelfs)
ds = xarray.open_mfdataset([file1,file2], engine='zarr')
```

### Specifying Endpoints

The following describes how to specify endpoints to get data from, rather than letting PelicanFS and the director determine the best cache. PelicanFS allows you to specify whether to read directly from the origin (bypassing data staging altogether) or to name a specific cache to stage data into.

**Note**
> If both direct reads and a specific cache are set, PelicanFS will use the specified cache and ignore the direct reads setting.


#### Enabling Direct Reads

Sometimes you might wish to read data directly from an origin rather than via a cache. To enable this at PelicanFileSystem creation, just pass in `direct_reads=True` to the constructor.

```python
pelfs = PelicanFileSystem("pelican://osg-htc.org", direct_reads=True)
```

#### Specifying a Cache

If you want to specify a specific cache to stage your data into (as opposed to the highest priority working cache), this can be done by passing in a cache URL during PelicanFileSystem construction via the `preferred_caches` variable:

```python
pelfs = PelicanFileSystem("pelican://osg-htc.org", preferred_caches=["https://cache.example.com"])
```

or

```python
pelfs = PelicanFileSystem("pelican://osg-htc.org", preferred_caches=["https://cache.example.com",
    "https://cache2.example.com", "+"])
```

Note that the special cache value `"+"` indicates that the provided preferred caches should be prepended to the
list of caches from the director.
