"""A form taller than the dock scrolls (advanced settings of a big tool), and the Run button / status stay reachable."""

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

tools = Path(tempfile.mkdtemp(prefix="lcscroll_"))
params = "".join("    p%02d: Annotated[float, Group('G%d'), Advanced()] = 1.0,\n" % (i, i // 10) for i in range(40))
(tools / "tall_lc_tools.py").write_text(
    "from typing import Annotated\n"
    "from labconstrictor_tools import Advanced, Group, Scalars, tool\n"
    "@tool('Tall')\n"
    "def tall(\n    base: float = 1.0,\n" + params + ") -> Scalars:\n    return {'base': base}\n"
)
subprocess.run(
    [sys.executable, "-m", "labconstrictor_tools", "register", "--name", "tall", "--prefix", sys.prefix,
     "--module", "tall_lc_tools", "--pythonpath", str(tools)],
    check=True, capture_output=True,
)  # fmt: skip

v = napari.Viewer(show=True)
v.window._qt_window.resize(1300, 700)
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


w.app_box.value = "tall"
pump(0.3)
w.tool_box.value = "Tall"
pump(0.8)
bar = w.form_scroll.verticalScrollBar()
expect("short_form_does_not_need_scrolling_until_advanced_is_open", bar.maximum() == 0 or bar.maximum() < 200, bar.maximum())
w.advanced_toggle.value = True
pump(0.8)
expect("form_scrolls_when_advanced_is_open", bar.maximum() > 200, bar.maximum())
expect("buttons_stay_in_view", w.details_button.isVisible() and w.cancel_button.isVisible() and w.status.isVisible())
bar.setValue(bar.maximum())
pump(0.4)
last = w.gui["p39"]
visible_rect = w.form_scroll.viewport().rect()
top = last.native.mapTo(w.form_scroll.viewport(), last.native.rect().topLeft())
expect("last_parameter_reachable_by_scrolling", visible_rect.contains(top), (top, visible_rect))
v.screenshot(str(EVIDENCE / "widget_scroll.png"), canvas_only=False)
print(json.dumps(report, indent=2))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
