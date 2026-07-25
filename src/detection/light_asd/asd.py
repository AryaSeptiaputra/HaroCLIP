import sys

import torch
import torch.nn as nn

from .loss import lossAV, lossV
from .model.Model import ASD_Model


class ASD(nn.Module):
    def __init__(self, lr=0.001, lrDecay=0.95, **kwargs):
        super(ASD, self).__init__()
        # Upstream hardcodes .cuda() (production/vast.ai always has CUDA); adapted to
        # fall back to CPU so this can also run for local verification without a GPU.
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = ASD_Model().to(self.device)
        self.lossAV = lossAV().to(self.device)
        self.lossV = lossV().to(self.device)

    def loadParameters(self, path):
        selfState = self.state_dict()
        loadedState = torch.load(path, map_location=lambda storage, loc: storage)
        for name, param in loadedState.items():
            origName = name
            if name not in selfState:
                name = name.replace("module.", "")
                if name not in selfState:
                    print("%s is not in the model." % origName)
                    continue
            if selfState[name].size() != loadedState[origName].size():
                sys.stderr.write(
                    "Wrong parameter length: %s, model: %s, loaded: %s"
                    % (origName, selfState[name].size(), loadedState[origName].size())
                )
                continue
            selfState[name].copy_(param)
