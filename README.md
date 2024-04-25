# PelicanFS

## Overview

PelicanFS is a file system interface (fsspec) for the Pelican Platform.  For more information about pelican, see our [main website](https://pelicanplatform.org) or [Github page](https://github.com/PelicanPlatform/pelican). For more information about fsspec, visit the [filesystem-spec](https://filesystem-spec.readthedocs.io/en/latest/index.html) page.


## Limitations

PelicanFS is built on top of the http fsspec implementation. As such, any functionality that isn’t available in the http implementation is also *not* available in PelicanFS.

### Installation

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


### Using PelicanFS

To use pelicanfs, first create a `PelicanFileSystem` and provide it with the url for the director of your data federation. As an example using the OSDF director

```python
from pelicanfs.core import PelicanFileSystem

pelfs = PelicanFileSystem("https://osdf-director.osg-htc.org/")
```

Once `pelfs` is pointed at your federation's director, fsspec commands can be applied to Pelican namespaces. For example:

```python
hello_world = pelfs.cat('/ospool/uc-shared/public/OSG-Staff/validation/test.txt')
print(hello_world)
```

### Getting an FSMap

Sometimes various systems that interact with an fsspec want a key-value mapper rather than a url. To do that, call the `PelicanMap` function with the namespace path and a `PelicanFileSystem` object rather than using the fsspec `get_mapper` call. For example

```python
from pelicanfs.core import PelicanFileSystem, PelicanMap

pelfs = PelicanFileSystem(“some-director-url”)
file1 = PelicanMap(“/namespace/file/1”, pelfs=pelfs)
file2 = PelicanMap(“/namespace/file/2”, pelfs=pelfs)
ds = xarray.open_mfdataset([file1,file2], engine='zarr')
```