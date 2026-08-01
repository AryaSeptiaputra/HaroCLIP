Vendored from https://github.com/google/fonts (`ofl/rubik/`), original source
https://github.com/googlefonts/rubik. SIL Open Font License, Version 1.1 (see
`OFL.txt`) — no Reserved Font Name declared in the upstream copyright line.

Upstream ships Rubik only as a variable font (`Rubik[wght].ttf`, `wght` axis
300–900) with a single legacy family name registered in the font's `name`
table: "Rubik Light" (the default named instance, weight 300). Named
instances above Light (Regular/Medium/SemiBold/Bold/ExtraBold/Black) exist
only as `fvar` axis positions, not as separate legacy family names — real
local testing (2026-08-01, Windows/DirectWrite via libass) confirmed this
directly: requesting font family "Rubik" or "Rubik Bold" from the unmodified
variable font silently fell back to Arial, while "Rubik Light" resolved
correctly. This is the same category of ambiguity the `DejaVu Sans Bold`
convention already used elsewhere in this project's caption rendering was
chosen to sidestep (see `src/captioning/subtitles.py`).

`Rubik-Bold-static.ttf` is the `wght=700` (Bold) named instance, frozen into
a standalone static font via `fonttools varLib.instancer` (`fontTools`
4.63.0), with its `name` table records renamed so both the legacy family
(nameID 1/4/6) and typographic family (nameID 16) read "Rubik Bold" /
"Rubik-Bold" — an unambiguous name any font matcher (fontconfig on Linux/
vast.ai, DirectWrite on Windows) can resolve directly, mirroring the
DejaVu Sans Bold approach rather than relying on variable-font instance
matching (real-render-verified to work via `fontselect: (Rubik Bold, 400, 0)
-> Rubik-Bold, 0, Rubik-Bold` after this rename; unmodified, it fell back to
Arial). No other modification — glyph outlines/metrics for the Bold weight
are upstream's as authored, only the `name` table and `fvar`/variable-font
machinery (dropped by instancing) differ from the original file.

Regenerating this file from a fresh upstream download:

```python
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

f = TTFont("Rubik[wght].ttf")  # https://raw.githubusercontent.com/google/fonts/main/ofl/rubik/Rubik%5Bwght%5D.ttf
inst = instancer.instantiateVariableFont(f, {"wght": 700.0})
name = inst["name"]
for nameID, value in [(1, "Rubik Bold"), (2, "Regular"), (4, "Rubik Bold"), (6, "Rubik-Bold"), (16, "Rubik Bold"), (17, "Regular")]:
    name.setName(value, nameID, 3, 1, 0x409)  # Windows platform
    name.setName(value, nameID, 1, 0, 0)       # Mac platform
inst.save("Rubik-Bold-static.ttf")
```
