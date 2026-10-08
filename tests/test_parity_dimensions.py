"""Host parity C15/C16 (NP-1): a stack or a multi-channel image given to a 2D tool (Axes("YX")) is refused with the
worker's sentence, the same one the command line, the notebook and the other hosts' workers give. The host never picks a plane
for the person (no silent choice of data)."""

import tempfile
from pathlib import Path

import _paths  # noqa: F401  (must come first)
import numpy as np
import tifffile
from _harness import choose, expect, finish, pump, register, run, viewer, widget

register("interactions", "labconstrictor_tools.examples.interactions")
work = Path(tempfile.mkdtemp(prefix="lcdims_"))

# a layer: a Z-stack for the synthetic tool "Plane mean" (Axes("YX"))
stack = viewer.add_image(np.ones((3, 32, 32), np.uint8), name="stack")
choose("synthetic", "Plane mean")
widget.gui.image.value = stack
pump(0.4)
run()
text = widget.status.text()
expect("stack_layer_is_refused", widget.last_task.status == "FAILED", text)
expect(
    "stack_layer_refusal_is_the_tools_sentence",
    "[wrong_dimensions] 'Image' must be a 2D image (YX) but got 3D with shape (3, 32, 32)" in text,
    text,
)
expect("stack_layer_refusal_names_no_plane", "plane" not in text.split("click Details")[0].lower(), text)

# a plain 2D layer still runs
flat = viewer.add_image(np.full((32, 32), 5, np.uint8), name="flat")
widget.gui.image.value = flat
pump(0.4)
run()
expect("2d_layer_runs", widget.last_task.status == "COMPLETE", widget.status.text())

# a colour (RGB) layer is 3D for a tool that did not ask for a channel
rgb = viewer.add_image(np.zeros((32, 32, 3), np.uint8), rgb=True, name="rgb")
widget.gui.image.value = rgb
pump(0.4)
run()
expect(
    "rgb_layer_is_refused_for_a_2d_tool",
    "[wrong_dimensions]" in widget.status.text() and "(32, 32, 3)" in widget.status.text(),
    widget.status.text(),
)

# a file with Z and C axes and PickChannel: the chosen channel is still a stack, so it is refused, not flattened
tifffile.imwrite(work / "zc.tif", np.ones((2, 3, 32, 32), np.uint8), imagej=True, metadata={"axes": "ZCYX"})
choose("interactions", "Mean of a channel")
widget.file_sources["image"].value = work / "zc.tif"
pump(0.6)
run()
expect(
    "channel_of_a_z_stack_file_is_refused",
    widget.last_task.status == "FAILED"
    and "[wrong_dimensions] 'Image' must be a 2D image (YX) but got 3D with shape (2, 32, 32)"
    in widget.status.text(),
    widget.status.text(),
)
finish()
