import torch
torch.utils.data.datapipes.utils.common.DILL_AVAILABLE = torch.utils._import_utils.dill_available()
from torchdata.datapipes.iter import IterableWrapper


if __name__ == '__main__':
    dp = IterableWrapper(["osdf:///chtc/PUBLIC/eturetsky/data/faces/"]).list_files_by_fsspec()
    print(list(dp))

    dp = IterableWrapper(["osdf:///chtc/PUBLIC/eturetsky/data/faces/"]).open_files_by_fsspec()
    for path, filestream in dp:
        print(path, filestream)