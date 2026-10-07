"""Copy as command: the button puts a terminal line / Python snippet on the clipboard; run for real it gives what the form gave."""

import json
import os
import shlex
import subprocess
import sys
import time

import _paths  # noqa: F401  (must come first)
import napari
import numpy as np
from _paths import EVIDENCE, HOME
from qtpy.QtWidgets import QApplication

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


env = {**os.environ, "LC_HOME": str(HOME), "LC_APPS_PATH": ""}
w.app_box.value = "synthetic"
pump(0.3)
w.tool_box.value = "Scalar echo"
pump(0.6)
w.gui["a"].value = 1.5
w.gui["b"].value = 2.25
pump(0.2)

line = w.copy_as_command("terminal")
expect("button_exists_with_a_menu", w.copy_button.menu() is not None and len(w.copy_button.menu().actions()) == 2)
expect("terminal_line_is_on_the_clipboard", line is not None and QApplication.clipboard().text() == line, line)
expect("line_names_app_tool_and_values", "run synthetic scalar_echo" in line and "a=1.5" in line and "b=2.25" in line, line)
done = subprocess.run(shlex.split(line), capture_output=True, text=True, env=env, encoding="utf-8")
out = done.stdout[: done.stdout.index("\n(results")] if "\n(results" in done.stdout else done.stdout
ok = done.returncode == 0 and json.loads(out)["status"] == "COMPLETE" and "3.75" in out
expect("copied_line_gives_the_same_result", ok, (done.returncode, done.stderr[-300:], out[-300:]))

snippet = w.copy_as_command("python")
expect("snippet_is_valid_python", snippet is not None and compile(snippet, "s", "exec") is not None, snippet)
done = subprocess.run([sys.executable, "-c", snippet], capture_output=True, text=True, env=env)
expect("copied_snippet_gives_the_same_result", done.returncode == 0 and "COMPLETE" in done.stdout and "3.75" in done.stdout, (done.stderr[-300:], done.stdout[-300:]))

# an image input has no file behind a layer: a placeholder and a note; the layer's own file is used when it has one
w.tool_box.value = "Image stats"
pump(0.6)
v.add_image(np.zeros((8, 8)), name="mem")
pump(0.3)
text = w.copy_as_command("terminal")
expect("image_without_a_file_gets_a_placeholder", "image=image.tif" in text and "replace the file for: image" in text, text)

w.status.setText("")
v.screenshot(str(EVIDENCE / "widget_copy_command.png"), canvas_only=False)
print(json.dumps(report, indent=2))
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
