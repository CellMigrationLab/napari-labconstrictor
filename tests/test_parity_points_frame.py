"""Host parity H05 (NP-6): points and outlines without `apply_to` are placed on the FIRST IMAGE input (PROTOCOL.md), not on the
first input of any type."""

import json
import tempfile
from pathlib import Path

import _paths  # noqa: F401  (must come first)
import numpy as np
from _harness import choose, expect, finish, pump, register, viewer, widget

from napari_labconstrictor._results import FileInput, ResultPresenter

register("roundtrip", "labconstrictor_tools.examples.roundtrip")
work = Path(tempfile.mkdtemp(prefix="lcframe_"))
(work / "pts.csv").write_text("y,x\n1,2\n3,4\n")
(work / "one.geojson").write_text(
    json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [0, 4], [0, 0]]]},
                }
            ],
        }
    )
)
layer = viewer.add_image(np.zeros((8, 8)), name="img", scale=(2.0, 3.0))
points = {"type": "points", "name": "p", "path": str(work / "pts.csv"), "n": 2}
outline = {"type": "shapes", "name": "o", "path": str(work / "one.geojson"), "n": 1}

# the first parameter is a number, the image comes second: the number has no frame
presenter = ResultPresenter(viewer, "a", {"threshold": 0.5, "img": layer}, image_inputs=["img"])
presenter.show(points)
presenter.show(outline)
expect(
    "points_use_the_first_image", tuple(viewer.layers["a:p"].scale) == (2.0, 3.0), viewer.layers["a:p"].scale
)
expect(
    "outlines_use_the_first_image",
    tuple(viewer.layers["a:o"].scale) == (2.0, 3.0),
    viewer.layers["a:o"].scale,
)

# a file given for the image counts as the image (its scale comes from the header)
file_input = FileInput.__new__(FileInput)
file_input.path, file_input.name, file_input.scale = work / "x.tif", "x.tif", (0.5, 0.25)
presenter = ResultPresenter(viewer, "b", {"n": 3, "img": file_input}, image_inputs=["img"])
presenter.show(points)
expect(
    "a_file_image_gives_its_scale",
    tuple(viewer.layers["b:p"].scale) == (0.5, 0.25),
    viewer.layers["b:p"].scale,
)

# apply_to wins over the first image
other = viewer.add_image(np.zeros((8, 8)), name="other", scale=(5.0, 7.0))
presenter = ResultPresenter(viewer, "c", {"img": layer, "other": other}, image_inputs=["img", "other"])
presenter.show({**points, "apply_to": "other"})
expect("apply_to_wins", tuple(viewer.layers["c:p"].scale) == (5.0, 7.0), viewer.layers["c:p"].scale)

# no image input at all: pixel units
presenter = ResultPresenter(viewer, "d", {"threshold": 0.5}, image_inputs=[])
presenter.show(points)
expect("no_image_means_pixels", tuple(viewer.layers["d:p"].scale) == (1.0, 1.0), viewer.layers["d:p"].scale)

# the widget gives the presenter the image parameters of the tool, in the tool's order
choose("roundtrip", "Region box")
widget._begin_run({"image": layer, "region": None}, {}, Path(tempfile.mkdtemp()))
expect(
    "widget_passes_image_inputs",
    widget.presenter.image_inputs == ("image", "region"),
    widget.presenter.image_inputs,
)
widget._set_running(False)
choose("roundtrip", "Echo points")
widget._begin_run({"points": None}, {}, Path(tempfile.mkdtemp()))
expect("a_table_tool_has_no_image_input", widget.presenter.image_inputs == (), widget.presenter.image_inputs)
widget._set_running(False)
pump(0.2)
finish()
