"""
A module to do top level testing on intake using pelicanfs
"""

import warnings

import intake
from distributed import Client  # LocalCluster
from ncar_jobqueue import NCARCluster

warnings.filterwarnings("ignore")


if __name__ == "__main__":

    # If not using NCAR HPC, use the LocalCluster
    # cluster = LocalCluster()
    cluster = NCARCluster()
    # cluster.scale(10)

    client = Client(cluster)

    catalog = intake.open_esm_datastore("file://examples/intake/resources/pelican-test-intake.json")

    catalog_subset = catalog.search(variable="FLNS", frequency="monthly")
    dsets = catalog_subset.to_dataset_dict()
