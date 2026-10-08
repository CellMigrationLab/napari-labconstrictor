"""ShapesOut: GeoJSON outlines become a Napari shapes layer (outer boundary per part, properties kept, Replace, scale, limit)."""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import _paths  # noqa: F401  (must come first)
import napari
import numpy as np
from _paths import EVIDENCE
from qtpy.QtWidgets import QApplication

subprocess.run(
    [sys.executable, "-m", "labconstrictor_tools", "register", "--name", "interactions", "--prefix", sys.prefix,
     "--module", "labconstrictor_tools.examples.interactions", "--version", "0.0"],
    check=True, capture_output=True,
)  # fmt: skip

v = napari.Viewer(show=True)
v.window._qt_window.resize(1500, 850)
qa = QApplication.instance()
_, w = v.window.add_plugin_dock_widget("napari-labconstrictor", "LabConstrictor tools")
report, failures = {}, []


def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        qa.processEvents()
        time.sleep(0.02)


def until(condition, timeout=120):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qa.processEvents()
        time.sleep(0.05)
    pump(0.4)


def run():
    w.last_task = None
    w.gui()
    until(lambda: w.last_task is not None and w.task is w.last_task)
    pump(0.8)


def expect(name, condition, detail=""):
    report[name] = bool(condition)
    if not condition:
        failures.append("%s %s" % (name, detail))


image = np.zeros((60, 80), np.uint8)
image[5:15, 5:15] = 255  # a blob
image[30:50, 30:50] = 255
image[36:44, 36:44] = 0  # a ring: the blob has a hole
image[5:12, 60:64] = 255  # two more blobs
image[20:27, 60:64] = 255
layer = v.add_image(image, name="blobs_in", scale=(0.5, 0.5))
w.app_box.value = "interactions"
pump(0.3)
w.tool_box.value = "Outline the blobs"
pump(1.0)
run()
names = [l.name for l in v.layers]
expect(
    "labels_and_shapes_layers", names == ["blobs_in", "interactions:blobs", "interactions:outlines"], names
)
shapes = v.layers["interactions:outlines"]
expect(
    "a_shape_per_blob",
    len(shapes.data) == 4 and w.last_task.status == "COMPLETE",
    (len(shapes.data), w.status.text()),
)
expect("polygons", all(t == "polygon" for t in shapes.shape_type), shapes.shape_type)
expect(
    "properties_kept",
    set(shapes.properties) >= {"label", "area"}
    and sorted(int(x) for x in shapes.properties["label"]) == [1, 2, 3, 4],
    dict(shapes.properties),
)
expect("scale_follows_the_image", tuple(shapes.scale[-2:]) == (0.5, 0.5), shapes.scale)
first = np.asarray(shapes.data[0])
expect(
    "coordinates_are_y_x_around_the_blob",
    first[:, 0].min() > 3 and first[:, 0].max() < 16 and first[:, 1].min() > 3 and first[:, 1].max() < 16,
    first.round(1).tolist(),
)
expect("no_fill", np.allclose(np.asarray(shapes.face_color)[:, 3], 0), shapes.face_color)
text = w.message_label.text()
expect("hole_note_in_the_message", "hole" in text and "1 outline" in text and "Outlined" in text, text)

w.gui["threshold"].value = 0.5
run()
v.layers["interactions:blobs"].visible = (
    False  # (image and labels results do not take the input scale yet: unrelated to shapes)
)
v.reset_view()
v.layers.selection.active = v.layers["interactions:outlines"]
pump(0.5)
v.screenshot(str(EVIDENCE / "widget_shapes.png"), canvas_only=False)

# Replace: a second run leaves one shapes layer (and one labels layer)
w.gui["threshold"].value = 0.9
run()
names = [l.name for l in v.layers]
expect("second_run_replaces", names == ["blobs_in", "interactions:blobs", "interactions:outlines"], names)

# the limit: more than 50 000 outlines show the first 50 000 and say so
from napari_labconstrictor._results import MAX_SHAPES, ResultPresenter

folder = Path(tempfile.mkdtemp(prefix="lcshapes_"))
count = MAX_SHAPES + 100
features = [
    {
        "type": "Feature",
        "properties": {"label": i},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [i % 300, i // 300],
                    [i % 300 + 0.5, i // 300],
                    [i % 300, i // 300 + 0.5],
                    [i % 300, i // 300],
                ]
            ],
        },
    }
    for i in range(count)
]
(folder / "many.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
presenter = ResultPresenter(v, "big", {}, replace=(), docks={})
note = presenter.show({"type": "shapes", "name": "many", "path": str(folder / "many.geojson"), "n": count})
expect(
    "limit_shows_the_first_50000",
    len(v.layers["big:many"].data) == MAX_SHAPES and "%d" % MAX_SHAPES in " ".join(presenter.messages),
    (note, presenter.messages),
)

# an empty result and a broken file are handled, not crashes
(folder / "empty.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
presenter.show({"type": "shapes", "name": "empty", "path": str(folder / "empty.geojson"), "n": 0})
expect("empty_result_is_an_empty_layer", "big:empty" in v.layers and len(v.layers["big:empty"].data) == 0)
(folder / "broken.geojson").write_text("{not json")
shown = presenter.show({"type": "shapes", "name": "broken", "path": str(folder / "broken.geojson"), "n": 1})
expect(
    "broken_file_is_reported", "could not display" in shown.lower() and "big:broken" not in v.layers, shown
)

print(json.dumps(report, indent=2))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
