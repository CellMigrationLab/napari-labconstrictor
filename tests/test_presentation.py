"""Presentation hints from the manifest: group headings and ordering, advanced settings behind a toggle, enabled_when."""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import _paths  # noqa: F401  (must come first)
import napari
from _paths import EVIDENCE
from qtpy.QtWidgets import QApplication

tools = Path(tempfile.mkdtemp(prefix="lcpresent_"))
(tools / "presentation_lc_tools.py").write_text(
    "from typing import Annotated, Literal, Optional\n"
    "from labconstrictor_tools import Advanced, EnabledWhen, Group, Min, Scalars, tool\n"
    "@tool('Probe')\n"
    "def probe(\n"
    "    mode: Annotated[Literal['threshold', 'cellpose'], Group('Segmentation')] = 'threshold',\n"
    "    size: Annotated[int, Min(0), Group('Images')] = 5,\n"
    "    count: Annotated[Optional[int], Group('Images')] = None,\n"
    "    method: Annotated[Literal['otsu', 'li'], Group('Segmentation'), EnabledWhen('mode', 'threshold')] = 'otsu',\n"
    "    fixed_seed: Annotated[bool, Group('Seed'), Advanced()] = False,\n"
    "    seed: Annotated[Optional[int], Group('Seed'), Advanced(), EnabledWhen('fixed_seed')] = None,\n"
    ") -> Scalars:\n"
    "    return {'mode': mode, 'method': method, 'size': size, 'count': count, 'seed': seed}\n"
)
subprocess.run(
    [sys.executable, "-m", "labconstrictor_tools", "register", "--name", "presentation", "--prefix", sys.prefix,
     "--module", "presentation_lc_tools", "--pythonpath", str(tools)],
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


def expect(name, condition, detail=""):
    report[name] = bool(condition)
    if not condition:
        failures.append("%s %s" % (name, detail))


def shown(widget):
    return not widget.native.isHidden()


w.app_box.value = "presentation"
pump(0.3)
w.tool_box.value = "Probe"
pump(0.5)
names = [x.name for x in w.gui if getattr(x, "name", None) and x.name != "call_button"]
expect("grouped_parameters_are_together", names.index("method") == names.index("mode") + 1, names)
expect(
    "advanced_parameters_come_last",
    names[-2:] == ["fixed_seed", "seed"] or names[-3:-1] == ["fixed_seed", "seed"],
    names,
)
headings = [
    x.value for x in w.gui if type(x).__name__ == "Label" and x.value in ("Segmentation", "Images", "Seed")
]
expect("group_headings_in_order", headings == ["Segmentation", "Images", "Seed"], headings)
expect(
    "parameter_named_like_a_container_method_works",
    "count" in w.unset_toggles and w.gui["count"].enabled is False,
)
expect("advanced_toggle_exists", w.advanced_toggle is not None and not w.advanced_toggle.value)
expect("advanced_hidden_by_default", not shown(w.gui.fixed_seed) and not shown(w.gui.seed))
w.advanced_toggle.value = True
pump(0.2)
expect("advanced_shown_when_toggled", shown(w.gui.fixed_seed) and shown(w.gui.seed))

expect("enabled_when_equals_true_initially", w.gui.method.enabled)
w.gui.mode.value = "cellpose"
pump(0.2)
expect("enabled_when_equals_greys_out", not w.gui.method.enabled)
w.gui.mode.value = "threshold"
pump(0.2)
expect("enabled_when_equals_back", w.gui.method.enabled)

expect("seed_greyed_without_fixed_seed", not w.gui.seed.enabled and not w.unset_toggles["seed"].enabled)
w.gui.fixed_seed.value = True
pump(0.2)
expect("seed_toggle_enabled_with_fixed_seed", w.unset_toggles["seed"].enabled and not w.gui.seed.enabled)
w.unset_toggles["seed"].value = True
w.gui.seed.value = 11
pump(0.2)
expect("seed_enabled_when_set", w.gui.seed.enabled)
w.last_task = None
w.gui()
end = time.time() + 60
while w.last_task is None and time.time() < end:
    qa.processEvents()
    time.sleep(0.05)
pump(0.8)
v.screenshot(str(EVIDENCE / "widget_presentation.png"), canvas_only=False)
expect(
    "run_receives_the_values",
    w.last_task.status == "COMPLETE" and "seed=11" in w.status.text(),
    w.status.text(),
)

print(json.dumps(report, indent=2))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
