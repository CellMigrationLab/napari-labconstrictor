"""Host parity N07 (NP-10): Copy as command names every modifier the command cannot carry, one comment line per parameter, after the
placeholder line and before the command (the order QuPath uses): the selection of RegionOf and the chosen channel.
"""

import tempfile
from pathlib import Path

import _paths  # noqa: F401  (must come first)
import numpy as np
import tifffile
from _harness import choose, expect, finish, pump, register, viewer, widget

register("interactions", "labconstrictor_tools.examples.interactions")
work = Path(tempfile.mkdtemp(prefix="lccopy_"))
tifffile.imwrite(work / "three.tif", np.ones((3, 8, 8), np.uint8), imagej=True, metadata={"axes": "CYX"})

choose("interactions", "Mean of a channel")
widget.file_sources["image"].value = work / "three.tif"
pump(0.6)
widget.channel_boxes["image"].value = "Channel 2"
pump(0.3)
for kind in ("terminal", "python"):
    text = widget.copy_as_command(kind)
    lines = text.split("\n")
    expect(
        "channel_note_%s" % kind,
        "# image: napari sent only channel 2; the command sends the whole file" in lines
        and f"{work / 'three.tif'}" in text,
        text,
    )
    expect(
        "note_comes_before_the_command_%s" % kind,
        lines.index("# image: napari sent only channel 2; the command sends the whole file")
        < next(i for i, line in enumerate(lines) if not line.startswith("#")),
        text,
    )

# an image with no file behind it: the placeholder line first, then the notes
widget.file_sources["image"].value = None
mem = viewer.add_image(np.zeros((8, 8)), name="mem")  # a layer without a file
choose("interactions", "Find bright spots")
widget.gui["image"].value = mem
shapes = viewer.add_shapes([np.array([[0, 0], [0, 5], [5, 5], [5, 0]])], shape_type="polygon", name="r")
viewer.layers.selection = {shapes}
widget.region_toggles["region"].value = True
pump(0.3)
text = widget.copy_as_command("terminal")
lines = text.split("\n")
expect(
    "placeholder_line_then_selection_note_then_command",
    lines[0].startswith("# replace the file for:")
    and lines[1] == "# region: the selection cannot be copied; save it as a label image and put its path here"
    and not lines[2].startswith("#"),
    text,
)
finish()
