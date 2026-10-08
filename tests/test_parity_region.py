"""Host parity C23 and C24 (NP-3, NP-4): a RegionOf selection is refused when the image is given as a file (a selection drawn on a
layer cannot be mapped onto an unknown file), and at most 65 535 objects are supported, with the same sentences as Fiji and
QuPath; the label image is 16 bit like theirs."""

import tempfile
from pathlib import Path

import _paths  # noqa: F401  (must come first)
import numpy as np
import tifffile
from _harness import choose, expect, finish, pump, register, viewer, widget

from napari_labconstrictor import _export

register("interactions", "labconstrictor_tools.examples.interactions")
work = Path(tempfile.mkdtemp(prefix="lcregion_"))
tifffile.imwrite(work / "field.tif", np.zeros((60, 80), np.uint8))

field = viewer.add_image(np.zeros((60, 80), np.uint8), name="field")
shapes = viewer.add_shapes(
    [np.array([[0, 0], [0, 25], [25, 25], [25, 0]]), np.array([[35, 55], [35, 75], [55, 75], [55, 55]])],
    shape_type="polygon",
    name="my region",
)
viewer.layers.selection = {shapes}
choose("interactions", "Find bright spots")
widget.region_toggles["region"].value = True
pump(0.3)
image_label = widget._label_of("image")
region_label = widget._label_of("region")


def export():
    try:
        return widget._export_inputs({"image": field, "region": None}, Path(tempfile.mkdtemp())), None
    except ValueError as error:
        return None, str(error)


# NP-3: a file chosen for the image
widget.file_sources["image"].value = work / "field.tif"
pump(0.4)
inputs, error = export()
expect(
    "file_image_with_a_selection_is_refused",
    inputs is None
    and error
    == "'%s': the selection belongs to an open image, but a file was chosen for '%s': open the image, or untick the selection"
    % (region_label, image_label),
    error,
)
widget.file_sources["image"].value = None
pump(0.4)
inputs, error = export()
expect("layer_image_with_a_selection_works", inputs is not None, error)
mask = tifffile.imread(inputs["region"]) if inputs else None
expect(
    "mask_is_16_bit_labels_1_to_N",
    mask is not None and mask.dtype == np.uint16 and sorted(set(mask.flat)) == [0, 1, 2],
    None if mask is None else (mask.dtype, sorted(set(mask.flat))),
)

# NP-4: the limit
expect("the_limit_is_65535", _export.MAX_REGION_OBJECTS == 65535, _export.MAX_REGION_OBJECTS)
_export.MAX_REGION_OBJECTS = (
    1  # two shapes are now one too many: the sentence is the real one with the real number printed
)
inputs, error = export()
expect(
    "too_many_objects_is_refused",
    inputs is None and error == "'%s': 2 objects are selected; at most 1 are supported" % region_label,
    error,
)
_export.MAX_REGION_OBJECTS = 65535
finish()
