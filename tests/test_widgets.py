"""Widget("slider") and Widget("radio"): a slider with a readout box, radio buttons, and the plain field when the hint cannot apply."""

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

tools = Path(tempfile.mkdtemp(prefix="lcwidgets_"))
(tools / "widgets_lc_tools.py").write_text(
    "from typing import Annotated, Literal, Optional\n"
    "from labconstrictor_tools import Image, Max, Min, Scalars, Widget, tool\n"
    "@tool('Look')\n"
    "def look(image: Image,\n"
    "         level: Annotated[float, Min(0), Max(1), Widget('slider')] = 0.25,\n"
    "         count: Annotated[int, Min(1), Max(9), Widget('slider')] = 3,\n"
    "         maybe: Annotated[Optional[float], Min(0), Max(1), Widget('slider')] = None,\n"
    "         mode: Annotated[Literal['a', 'b', 'c'], Widget('radio')] = 'b',\n"
    "         ) -> Scalars:\n"
    "    return {'level': level, 'count': count, 'mode': mode, 'maybe': -1 if maybe is None else maybe}\n"
)
subprocess.run(
    [sys.executable, "-m", "labconstrictor_tools", "register", "--name", "widgets", "--prefix", sys.prefix,
     "--module", "widgets_lc_tools", "--pythonpath", str(tools)],
    check=True, capture_output=True,
)  # fmt: skip

v = napari.Viewer(show=True)
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


v.add_image(np.zeros((16, 16)), name="src")
w.app_box.value = "widgets"
pump(0.3)
w.tool_box.value = "Look"
pump(0.8)
g = w.gui
cls = lambda name: type(g[name]).__name__
expect("float_slider", cls("level") == "FloatSlider", cls("level"))
expect("int_slider", cls("count") == "Slider", cls("count"))
expect("radio_buttons", cls("mode") == "RadioButtons", cls("mode"))
expect("nullable_number_stays_plain", cls("maybe") != "FloatSlider", cls("maybe"))
expect("defaults_kept", abs(g["level"].value - 0.25) < 1e-9 and g["count"].value == 3 and g["mode"].value == "b", (g["level"].value, g["count"].value, g["mode"].value))
expect("slider_respects_bounds", g["level"].min == 0 and g["level"].max == 1 and g["count"].max == 9, (g["level"].min, g["level"].max, g["count"].max))

g["level"].value = 0.8
g["count"].value = 7
g["mode"].value = "c"
pump(0.3)
w.last_task = None
g()
until(lambda: w.last_task is not None and w.task is w.last_task)
text = w.status.text()
expect("run_gets_the_widget_values", w.last_task.status == "COMPLETE" and "0.8" in text and "7" in text and "mode" in text and "c" in text, text)

v.window._qt_window.resize(1400, 800)
pump(0.5)
v.screenshot(str(EVIDENCE / "widget_slider_radio.png"), canvas_only=False)
print(json.dumps(report, indent=2))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
