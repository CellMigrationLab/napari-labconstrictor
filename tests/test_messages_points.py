"""Message and points outputs, with the dropdown / replace / accordion hints, using the example app that ships with labconstrictor-tools."""

import json
import subprocess
import sys
import time

import _paths  # noqa: F401  (must come first)
import napari
from _paths import EVIDENCE
from qtpy.QtWidgets import QApplication

subprocess.run(
    [sys.executable, "-m", "labconstrictor_tools", "register", "--name", "interactions", "--prefix", sys.prefix,
     "--module", "labconstrictor_tools.examples.interactions", "--version", "0.0"],
    check=True, capture_output=True,
)  # fmt: skip

v = napari.Viewer(show=True)
v.window._qt_window.resize(1400, 800)
qa = QApplication.instance()
_, w = v.window.add_plugin_dock_widget("napari-labconstrictor", "LabConstrictor tools")
report, failures = {}, []


def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        qa.processEvents()
        time.sleep(0.02)


def until(condition, timeout=60):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qa.processEvents()
        time.sleep(0.05)
    pump(0.3)


def expect(name, condition, detail=""):
    report[name] = bool(condition)
    if not condition:
        failures.append("%s %s" % (name, detail))


def run():
    w.last_task = None
    w.gui()
    until(lambda: w.last_task is not None and w.task is w.last_task)
    pump(0.6)


w.app_box.value = "interactions"
pump(0.3)
w.tool_box.value = "Show a shape"
pump(0.8)
box = w._choice_boxes["shape"]
until(lambda: not box.native.isHidden())
expect(
    "dropdown_without_depends_is_filled_at_once",
    list(box.choices) == ["", "square", "bar", "dot"],
    list(box.choices),
)
expect("message_hidden_before_a_run", w.message_label.isHidden())
box.value = "square"
run()
names = [layer.name for layer in v.layers]
expect("image_and_points_layers", names == ["interactions:picture", "interactions:corners"], names)
pts = v.layers["interactions:corners"]
expect(
    "points_have_two_rows_and_properties",
    pts.data.shape == (2, 2) and list(pts.properties["corner"]) == ["top-left", "bottom-right"],
    (pts.data, pts.properties),
)
expect(
    "message_is_shown",
    not w.message_label.isHidden() and "square" in w.message_label.text(),
    w.message_label.text(),
)
expect("status_mentions_the_points", "points 'corners' (2)" in w.status.text(), w.status.text())
v.screenshot(str(EVIDENCE / "widget_messages_points_1.png"), canvas_only=False)
box.value = "bar"
run()
names = [layer.name for layer in v.layers]
expect(
    "second_run_replaces_picture_and_points",
    names == ["interactions:picture", "interactions:corners"] and "bar" in w.message_label.text(),
    (names, w.message_label.text()),
)
expect("points_follow_the_new_shape", v.layers["interactions:corners"].data.shape == (2, 2))
v.screenshot(str(EVIDENCE / "widget_messages_points_2.png"), canvas_only=False)
w.unset_toggles["shape"].value = True
w.gui.shape.value = "triangle"
run()
expect(
    "a_failure_shows_a_readable_error_and_hides_the_old_message",
    "Unknown shape" in w.status.text() and w.message_label.isHidden(),
    (w.status.text(), w.message_label.isHidden()),
)
print(json.dumps(report, indent=2))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
