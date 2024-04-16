# PelicanFS

## Overview

PelicanFS is a file system interface (fsspec) for the Pelican Platform.  For more information about the Pelican Platform, please visit the [Pelican Platform](https://pelicanplatform.org) and the [Pelican Platform Github](https://github.com/PelicanPlatform/pelican) pages. For more information about fsspec, visit the [filesystem-spec](https://filesystem-spec.readthedocs.io/en/latest/index.html) page.


## Limitations

PelicanFS is built on top of the http fsspec implementation. As such, any functionality that isn’t available in the http implementation is also *not* available in PelicanFS.

### Installation

To install pelican, run:```pip install pelicanfs```### Using PelicanFS

To use pelicanfs, first create a `PelicanFileSystem` and provide it with the url for the director of your data federation. As an example using the OSDF director

```python
from pelicanfs.core import PelicanFileSystem

pelfs = PelicanFileSystem("https://osdf-director.osg-htc.org/")
```

From there, use `pelfs` as you would an http fsspec using a namespace path as the url path. For example:

```python
hello_world = pelfs.cat('/ospool/uc-shared/public/OSG-Staff/validation/test.txt')
print(hello_world)
```

### Getting an FSMap

Sometimes various systems that interact with an fsspec want a key-value mapper rather than a url. To do that, call the `PelicanMap` function with the namespace path and a `PelicanFileSystem` object rather than using the fsspec `get_mapper` call. For example

```python
from pelicanfs.core import PelicanFileSystem, PelicanMap

pelfs = PelicanFileSystem(“some-director-url”)
file1 = PelicanMap(“namespace/file/1”, pelfs=pelfs)
file2 = PelicanMap(“namespace/file/2”, pelfs=pelfs)
ds = xarray.open_mfdataset([file1,file2], engine='zarr')
```