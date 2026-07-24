Vendored from https://github.com/Junhua-Liao/Light-ASD (MIT License, see `LICENSE`).

`model/Model.py`, `model/Encoder.py`, `model/Classifier.py`, `loss.py` are unmodified
except import paths (upstream uses flat imports assuming a repo-root working
directory; this vendored copy uses relative imports so it works as a normal Python
subpackage).

`asd.py` is adapted from upstream's `ASD.py`: kept `__init__` (model construction) and
`loadParameters` (dropped `saveParameters` — never called, training-only); **dropped**
`train_network` and the CSV/mAP-focused `evaluate_network` method, since they are
training/offline-benchmark code paths this project never calls, and `evaluate_network`
pulls in `pandas`/`tqdm` purely for CSV output we don't need. One functional change:
`loadParameters` passes `map_location=lambda storage, loc: storage` to `torch.load` (
upstream omits it) so a checkpoint saved from a CUDA run can still be deserialized on a
machine without CUDA visible — the loaded tensors are copied into the already-correctly-
placed `self.state_dict()` tensors either way, so this doesn't change where the model
ultimately runs, only makes loading itself more portable.

`weight/pretrain_AVA_CVPR.model` is the upstream pretrained checkpoint (AVA-
ActiveSpeaker, ~94.06% mAP per upstream's README), vendored as-is.
