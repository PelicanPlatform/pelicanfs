import warnings

warnings.filterwarnings("ignore")

import intake
import numpy as np
import pandas as pd
import xarray as xr
#import hvplot.pandas, hvplot.xarray
#import holoviews as hv
from distributed import LocalCluster, Client
from ncar_jobqueue import NCARCluster
#hv.extension('bokeh')


if __name__ == '__main__':

    # If not using NCAR HPC, use the LocalCluster
    #cluster = LocalCluster()
    cluster = NCARCluster()
    cluster.scale(10)

    client = Client(cluster)

    catalog = intake.open_esm_datastore(
        'file://examples/intake/resources/pelican-test-intake.json'
    )

    catalog_subset = catalog.search(variable='FLNS', frequency='monthly')
    dsets = catalog_subset.to_dataset_dict()