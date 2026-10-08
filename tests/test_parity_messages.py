"""Host parity B01, F01, I11 (NP-2): the refusals the host makes before a run use the sentences of the Tools worker
(`[missing_parameter] 'L' is required`, `[file_not_found] image file not found: P`) and the channel sentence of the other hosts.
"""

import tempfile
from pathlib import Path

import _paths  # noqa: F401  (must come first)
import numpy as np
import tifffile
from _harness import choose, expect, finish, pump, register, widget

register("interactions", "labconstrictor_tools.examples.interactions")
work = Path(tempfile.mkdtemp(prefix="lcmsg_"))
tifffile.imwrite(work / "three.tif", np.ones((3, 8, 8), np.uint8), imagej=True, metadata={"axes": "CYX"})

choose("synthetic", "Image stats")
label = widget._label_of("image")
pump(0.3)
widget.gui()
pump(0.5)
expect(
    "missing_image_template",
    widget.status.text() == "⚠ [missing_parameter] '%s' is required: open an image or choose a file" % label,
    widget.status.text(),
)

missing = work / "gone.tif"
widget.file_sources["image"].value = missing
pump(0.4)
widget.gui()
pump(0.5)
expect(
    "missing_file_template",
    widget.status.text() == "⚠ [file_not_found] image file not found: %s" % missing,
    widget.status.text(),
)

choose("interactions", "Mean of a channel")
widget.file_sources["image"].value = work / "three.tif"
pump(0.6)
box = widget.channel_boxes["image"]
box.choices = [
    "Channel 1",
    "Channel 2",
    "Channel 3",
    "Channel 4",
]  # the chooser offers one more than the file has
box.value = "Channel 4"
pump(0.3)
widget.gui()
pump(0.5)
expect(
    "channel_beyond_the_file_sentence",
    widget.status.text()
    == "⚠ '%s': channel 4 was asked for, but the image has 3 channels" % widget._label_of("image"),
    widget.status.text(),
)
finish()
