"""Interaction hints from the manifest: ChoicesFrom (dropdown filled by another tool), ClearAfterRun, Collapsed (accordion), Replace."""

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

tools = Path(tempfile.mkdtemp(prefix="lcinteract_"))
workdir = Path(tempfile.mkdtemp(prefix="lcinteract_data_"))
(tools / "interact_lc_tools.py").write_text(
    "from pathlib import Path\n"
    "from typing import Annotated, Optional\n"
    "import numpy as np\n"
    "from labconstrictor_tools import Affine, ApplyTo, Image, ChoicesFrom, ClearAfterRun, Collapsed, Folder, Group, ImageOut, Name, Replace, Scalars, TableOut, tool\n"
    "@tool('List options')\n"
    "def options(folder: Folder) -> Scalars:\n"
    "    f = Path(folder) / 'options.txt'\n"
    "    return {'choices': f.read_text().split() if f.exists() else []}\n"
    "@tool('Warp')\n"
    "def warp(image: Image, shift: float = 1.0) -> Annotated[Affine, ApplyTo('image'), Name('alignment'), Replace()]:\n"
    "    m = np.eye(3); m[0, 2] = shift\n"
    "    return m\n"
    "@tool('Answer')\n"
    "def answer(folder: Folder,\n"
    "           guess: Annotated[Optional[str], ChoicesFrom('options', depends=['folder']), ClearAfterRun()] = None,\n"
    "           extra: Annotated[int, Group('More options'), Collapsed()] = 1,\n"
    "           ) -> tuple[Annotated[ImageOut, Name('view'), Replace()], Annotated[TableOut, Name('log'), Replace()], Scalars]:\n"
    "    guess = guess or ''\n"
    "    n = len(guess) + 1\n"
    "    return np.full((8 * n, 8 * n), n, dtype=np.uint8), {'guess': [guess]}, {'got': guess}\n"
)
subprocess.run(
    [sys.executable, "-m", "labconstrictor_tools", "register", "--name", "interact", "--prefix", sys.prefix,
     "--module", "interact_lc_tools", "--pythonpath", str(tools)],
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
    return condition()


def expect(name, condition, detail=""):
    report[name] = bool(condition)
    if not condition:
        failures.append("%s %s" % (name, detail))


def shown(widget):
    return not widget.native.isHidden()


def run():
    w.last_task = None
    w.gui()
    until(lambda: w.last_task is not None)
    pump(0.5)


w.app_box.value = "interact"
pump(0.3)
w.tool_box.value = "Answer"
pump(0.5)
box = w._choice_boxes["guess"]
expect("dropdown_hidden_until_the_source_can_answer", not shown(box) and shown(w.gui.guess))

# 1. choices follow the depends parameters
(workdir / "options.txt").write_text("Control Treated")
w.gui.folder.value = workdir
until(lambda: shown(box))
expect(
    "dropdown_shown_with_the_options", shown(box) and not shown(w.gui.guess), [shown(box), shown(w.gui.guess)]
)
expect("dropdown_options", list(box.choices) == ["", "Control", "Treated"], list(box.choices))
box.value = "Treated"
pump(0.2)
expect("dropdown_choice_is_the_value_sent", w.gui.guess.value == "Treated")
expect("picking_an_option_ticks_set", w.unset_toggles["guess"].value is True)

# 2. Collapsed group starts folded, opens on click, folds again
heading = w._group_heading["More options"]
expect("accordion_folded_at_start", not shown(w.gui.extra))
heading.changed.emit(True)
pump(0.2)
expect("accordion_opens", shown(w.gui.extra) and heading.text.startswith("▾"))
heading.changed.emit(True)
pump(0.2)
expect("accordion_folds_again", not shown(w.gui.extra) and heading.text.startswith("▸"))

# 3. Replace: two runs leave one layer and one table dock; ClearAfterRun empties the guess
run()
expect("first_run_ok", w.last_task.status == "COMPLETE" and "got=Treated" in w.status.text(), w.status.text())
layers = [l.name for l in v.layers]
expect("one_layer_after_first_run", layers == ["interact:view"], layers)
expect(
    "guess_cleared_after_the_run",
    w.gui.guess.value == "" and box.value == "" and w.unset_toggles["guess"].value is False,
    (w.gui.guess.value, box.value, w.unset_toggles["guess"].value),
)
box.value = "Control"
pump(0.2)
run()
layers = [l.name for l in v.layers]
expect("replace_keeps_one_layer", layers == ["interact:view"], layers)
expect(
    "replaced_layer_has_the_new_data",
    v.layers["interact:view"].data.shape == (8 * 8, 8 * 8),
    v.layers["interact:view"].data.shape,
)
docks = [k for k in w._docks]
expect("replace_keeps_one_table_dock", len(docks) == 1 and docks == [("interact", "log")], docks)

# 4. a failed source (folder removed) falls back to the text field
(workdir / "options.txt").unlink()
w.gui.folder.value = workdir  # same value: no change event, force a new question
w._schedule_choices(0)
until(lambda: shown(w.gui.guess))
expect("falls_back_to_text_when_no_options", shown(w.gui.guess) and not shown(box))

# 5. a failed run does not clear the field
w.unset_toggles["guess"].value = True
w.gui.guess.value = "keep me"
w.gui.folder.value = Path(tempfile.gettempdir()) / "does_not_exist_lc"
pump(0.3)
run()
expect(
    "failed_run_keeps_the_guess",
    w.last_task.status != "COMPLETE" and w.gui.guess.value == "keep me",
    (w.last_task.status, w.gui.guess.value),
)

# 6. Replace on an affine output: two runs leave one overlay layer
import numpy as np

v.layers.clear()
v.add_image(np.random.default_rng(0).random((32, 32)), name="src")
w.tool_box.value = "Warp"
pump(0.5)
for shift in (1.0, 4.0):
    w.gui.shift.value = shift
    run()
overlays = [l.name for l in v.layers if l.name == "interact:alignment"]
expect(
    "affine_replace_keeps_one_overlay",
    len(overlays) == 1 and w.last_task.status == "COMPLETE",
    [l.name for l in v.layers],
)
expect(
    "affine_overlay_has_the_new_matrix",
    abs(v.layers["interact:alignment"].affine.affine_matrix[0, 2] - 4.0) < 1e-6,
    v.layers["interact:alignment"].affine.affine_matrix,
)

v.screenshot(str(EVIDENCE / "widget_interactions.png"), canvas_only=False)
print(json.dumps(report, indent=2))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
