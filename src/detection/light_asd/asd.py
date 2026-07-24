import sys

import torch
import torch.nn as nn

from .loss import lossAV, lossV
from .model.Model import ASD_Model


class ASD(nn.Module):
    def __init__(self, lr=0.001, lrDecay=0.95, **kwargs):
        super(ASD, self).__init__()
        self.model = ASD_Model().cuda()
        self.lossAV = lossAV().cuda()
        self.lossV = lossV().cuda()

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
