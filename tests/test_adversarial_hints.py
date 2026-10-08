"""Hostile and odd inputs for the interaction hints and the message / points outputs."""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import _paths  # noqa: F401  (must come first)
import napari
from qtpy.QtWidgets import QApplication

tools = Path(tempfile.mkdtemp(prefix="lcadv_"))
(tools / "adv_lc_tools.py").write_text(r'''
import time
from typing import Annotated, Optional
import numpy as np
from labconstrictor_tools import ChoicesFrom, ClearAfterRun, ImageOut, MessageOut, Name, PointsOut, Replace, Scalars, ToolError, tool

@tool("Options: broken")
def broken_options(mode: str = "x") -> Scalars:
    raise RuntimeError("the source tool is broken")
@tool("Options: not a list")
def odd_options(mode: str = "x") -> Scalars:
    return {"choices": "not-a-list"}
@tool("Options: many")
def many_options(mode: str = "x") -> Scalars:
    return {"choices": ["opt %05d é中" % i for i in range(5000)] + ["", "dup", "dup", 7]}
@tool("Pick")
def pick(mode: Annotated[str, ChoicesFrom("many_options")] = "x",
         a: Annotated[Optional[str], ChoicesFrom("broken_options"), ClearAfterRun()] = None,
         b: Annotated[Optional[str], ChoicesFrom("odd_options")] = None,
         c: Annotated[Optional[str], ChoicesFrom("many_options"), ClearAfterRun()] = None) -> Scalars:
    return {"mode": mode, "a": a or "", "c": c or ""}
@tool("Dims")
def dims(ndim: int = 2, rows: int = 3, text_length: int = 10, fail: bool = False) -> tuple[
        Annotated[ImageOut, Name("img"), Replace()], Annotated[PointsOut, Name("pts"), Replace()], Annotated[MessageOut, Name("msg")]]:
    if fail:
        raise ToolError("boom", "deliberate failure")
    import pandas as pd
    shape = (16,) * ndim
    pts = pd.DataFrame({"y": np.arange(rows, dtype=float), "x": np.arange(rows, dtype=float)}) if rows else pd.DataFrame({"y": [], "x": []})
    return np.ones(shape, np.uint8), pts, ("word " * text_length) + "<b>not bold</b> [link](http://example.com) **bold**"
''')
subprocess.run([sys.executable, "-m", "labconstrictor_tools", "register", "--name", "adv", "--prefix", sys.prefix,
                "--module", "adv_lc_tools", "--pythonpath", str(tools)], check=True, capture_output=True)

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


def until(cond, timeout=60):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        qa.processEvents()
        time.sleep(0.05)
    pump(0.3)


def expect(name, ok, detail=""):
    report[name] = bool(ok)
    if not ok:
        failures.append("%s %s" % (name, detail))


def run():
    w.last_task = None
    w.gui()
    until(lambda: w.last_task is not None and w.task is w.last_task)
    pump(0.6)


shown = lambda x: not x.native.isHidden()
w.app_box.value = "adv"
pump(0.3)
w.tool_box.value = "Pick"
pump(0.8)
until(lambda: shown(w._choice_boxes["mode"]))
many = list(w._choice_boxes["mode"].choices)
expect("5000_options_with_unicode_fill_a_dropdown", len(many) >= 5000 and any("中" in c for c in many), len(many))
expect("non_string_option_becomes_text", "7" in many, many[-4:])
expect("broken_source_keeps_the_text_field", shown(w.gui.a) and not shown(w._choice_boxes["a"]))
expect("source_returning_a_non_list_keeps_the_text_field", shown(w.gui.b) and not shown(w._choice_boxes["b"]))
expect("a_failed_choices_question_is_said_in_the_status_line", "gave no choices" in w.gui.a.native.toolTip() and "type the value" in w.gui.a.native.toolTip(), w.gui.a.native.toolTip())
expect("the_form_is_still_usable", w.gui.call_button.enabled)
expect("a_default_the_source_does_not_list_is_kept_not_blanked", w.gui.mode.value == "x" and w._choice_boxes["mode"].value == "x", (w.gui.mode.value, w._choice_boxes["mode"].value))
w._choice_boxes["c"].value = "opt 00042 é中"
w.gui.a.value = "free text"
w.unset_toggles["a"].value = True
run()
expect("run_with_unicode_choice_and_free_text_ok", w.last_task.status == "COMPLETE" and "opt 00042" in w.status.text(), w.status.text())
expect("clear_after_run_resets_dropdown_and_text_field", w._choice_boxes["c"].value == "" and w.gui.a.value == "" and not w.unset_toggles["a"].value)

w.tool_box.value = "Dims"
pump(0.8)
run()
expect("first_run_one_layer_one_points_layer", [l.name for l in v.layers] == ["adv:img", "adv:pts"], [l.name for l in v.layers])
expect("message_text_is_not_executed_as_html", "not bold" in w.message_label.text() and w.message_label.isVisible())
w.gui.ndim.value = 3
run()
expect("replace_survives_a_change_of_dimensions", [l.name for l in v.layers].count("adv:img") == 1 and v.layers["adv:img"].data.ndim == 3, [(l.name, getattr(l.data, "ndim", None)) for l in v.layers])
w.gui.rows.value = 0
run()
expect("zero_points_is_not_a_crash", w.last_task.status == "COMPLETE" and "could not" not in w.status.text(), w.status.text())
expect("zero_points_leave_an_empty_layer", "adv:pts" in [l.name for l in v.layers] and len(v.layers["adv:pts"].data) == 0)
w.gui.rows.value = 50000
run()
expect("fifty_thousand_points", w.last_task.status == "COMPLETE" and len(v.layers["adv:pts"].data) == 50000, w.status.text())
w.gui.rows.value = 3
w.gui.text_length.value = 3000
run()
expect("very_long_message_does_not_break_the_dock", w.last_task.status == "COMPLETE" and w.gui.call_button.enabled and w.details_button.isVisible())
w.gui.fail.value = True
run()
expect("failure_hides_the_previous_message_and_keeps_layers", w.message_label.isHidden() and "deliberate failure" in w.status.text() and len(v.layers) == 2, (w.status.text(), len(v.layers)))
# a layer the user renamed is never replaced
v.layers["adv:img"].name = "keep me"
w.gui.fail.value = False
run()
names = [l.name for l in v.layers]
expect("renamed_layer_is_kept_and_a_new_one_made", "keep me" in names and "adv:img" in names, names)
# switching tool during a run must not touch the other form
w.tool_box.value = "Pick"
pump(0.5)
expect("no_stale_widgets_after_switching", w.gui is not None and "mode" in [x.name for x in w.gui])
print(json.dumps(report, indent=2))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
