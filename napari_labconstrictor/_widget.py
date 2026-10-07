"""ONE napari dock widget for every installed LabConstrictor app.

registry (cached JSON) -> app/tool choosers -> schema -> synthesized signature -> magicgui form -> worker -> typed results
"""

import shutil
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
from labconstrictor_tools import log, registry, runs
from labconstrictor_tools.protocol import JOB_DIR_KEY
from magicgui import magicgui
from magicgui import widgets as mw
from qtpy.QtCore import QObject, Qt, QTimer, Signal
from qtpy.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from ._results import COULD_NOT_DISPLAY, FileInput, ResultPresenter
from ._schema import presentation_order, rule_satisfied, signature_from_schema
from ._units import anisotropy_note, microns_per_pixel_yx, microns_yx_from_tiff
from ._workers import WorkerCache

MAX_EXPORT_BYTES = 4 * 1024**3  # refuse to write a layer larger than this to a temporary TIFF
CANCEL_GRACE_S = 3.0  # how long a tool gets to honour Cancel before its worker is killed
_FINISH_TEXT = {"CANCELED": "cancelled"}
_NO_RESULT_CODES = ("no_match", "no_result")  # an outcome ("nothing found"), not a fault: shown as a notice


def _is_set(path):
    return path is not None and str(path) not in ("", ".")


def _tool_names(schema):
    """[(name shown in the chooser, tool)]: the label, or `label (id)` when two tools share a label (never pick the wrong one)."""
    labels = [t["label"] for t in schema["tools"]]
    return [
        (t["label"] if labels.count(t["label"]) == 1 else "%s (%s)" % (t["label"], t["id"]), t)
        for t in schema["tools"]
    ]


class _Signals(QObject):
    progress = Signal(object, object)
    done = Signal(object)
    choices = Signal(object, object)  # (request number, list of options or None)


class LabConstrictorWidget(QWidget):
    def __init__(self, napari_viewer=None):
        super().__init__()
        self.viewer = napari_viewer
        self.apps, self.schemas = {}, {}
        self.gui = None  # the magicgui form of the current tool
        self.file_sources = {}  # image parameter -> "or file" widget
        self.unset_toggles = {}  # nullable parameter -> "set" checkbox
        self.advanced_toggle = (
            None  # "Show advanced settings" checkbox (only when a tool has advanced parameters)
        )
        self._members = {}  # parameter -> its widgets (value widget, "or file" row, "set" checkbox)
        self.task = self.worker = self.last_task = None
        self.presenter = None
        self.last_record = None
        self.last_details = ""
        self._request = {}
        self._run_app = self._run_tool = (
            None  # what the running task was started with (the choosers may change meanwhile)
        )
        self.timings = {}
        self._workers = WorkerCache()
        self._signals = _Signals()
        self._signals.progress.connect(self._on_progress)
        self._signals.done.connect(self._on_done)
        self._signals.choices.connect(self._on_choices)
        self._choice_boxes = {}  # ChoicesFrom parameter -> its dropdown
        self._choice_seq = {}  # parameter -> number of the newest question; older answers are dropped
        self._choice_timer = QTimer(self)
        self._choice_timer.setSingleShot(True)
        self._choice_timer.timeout.connect(self._resolve_choices)
        self._docks = {}  # (app, table name) -> dock widget, so that Replace can swap it
        self._group_members = {}  # collapsible group -> widgets
        self._build_layout()
        if self.viewer is not None:
            # Layer choosers must follow the layer list even if this widget was built before it was docked.
            self.viewer.layers.events.inserted.connect(self._refresh_layer_choices)
            self.viewer.layers.events.removed.connect(self._refresh_layer_choices)
        self.rescan()

    # ---- layout -------------------------------------------------------
    def _build_layout(self):
        self.app_box = mw.ComboBox(label="Application")
        self.tool_box = mw.ComboBox(label="Tool")
        self.description = QLabel("")
        self.description.setWordWrap(True)
        self.form_holder = QVBoxLayout()
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        self.status = QLabel("idle")
        self.status.setWordWrap(True)
        self.message_label = QLabel("")  # message results of the last run (markdown); hidden when there is none
        self.message_label.setWordWrap(True)
        self.message_label.setTextFormat(Qt.MarkdownText)
        self.message_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.message_label.setStyleSheet("QLabel { border-left: 3px solid #5a9fd4; padding: 4px 8px; }")
        self.message_label.setVisible(False)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.rescan_button = QPushButton("Rescan apps")
        self.reuse_box = QCheckBox("Keep the worker running between runs (faster repeat runs)")
        self.reuse_box.setChecked(True)
        self.restart_button = QPushButton("Restart worker")
        self.details_button = QPushButton("Details…")
        self.details_button.setEnabled(False)
        self.details_button.setToolTip("Error details, worker output and the run record of the last run")
        buttons = QHBoxLayout()
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.rescan_button)
        buttons.addWidget(self.restart_button)
        buttons.addWidget(self.details_button)
        layout = QVBoxLayout(self)
        for widget in (self.app_box.native, self.tool_box.native, self.description):
            layout.addWidget(widget)
        # The form can be taller than the dock (advanced settings of a big tool): it scrolls, while the progress bar, status and
        # buttons below stay in view.
        form_container = QWidget()
        form_container.setLayout(self.form_holder)
        self.form_holder.setContentsMargins(0, 0, 0, 0)
        self.form_holder.addStretch(1)  # a short form sits at the top instead of being spread over the dock
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QFrame.NoFrame)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.form_scroll.setMinimumHeight(180)
        self.form_scroll.setWidget(form_container)
        layout.addWidget(self.form_scroll, 1)
        layout.addWidget(self.bar)
        # Status and message can be long (a readout, many values): they scroll in a box of limited height, so they never squeeze the form.
        outcome = QWidget()
        outcome_layout = QVBoxLayout(outcome)
        outcome_layout.setContentsMargins(0, 0, 0, 0)
        outcome_layout.addWidget(self.message_label)  # the readout first: it is what the person came for
        outcome_layout.addWidget(self.status)
        outcome_layout.addStretch(1)
        self.outcome_scroll = QScrollArea()
        self.outcome_scroll.setWidgetResizable(True)
        self.outcome_scroll.setFrameShape(QFrame.NoFrame)
        self.outcome_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.outcome_scroll.setMaximumHeight(320)
        self.outcome_scroll.setMinimumHeight(110)
        self.outcome_scroll.setWidget(outcome)
        layout.addWidget(self.outcome_scroll, 1)
        layout.addWidget(self.reuse_box)
        layout.addLayout(buttons)
        self.cancel_button.clicked.connect(self.cancel)
        self.details_button.clicked.connect(self._show_details)
        self.rescan_button.clicked.connect(self.rescan)
        self.restart_button.clicked.connect(lambda *_: self._workers.discard(self.app_box.value))
        self._stderr_mark: int | None = 0  # how much worker output existed when the current run started
        self._reaper = QTimer(self)
        self._reaper.timeout.connect(self._workers.reap_idle)
        self._reaper.start(30_000)
        # closing the Napari window deletes this widget without calling closeEvent: stop the workers then too (not only when the
        # whole Python process ends); bound to the cache object, which outlives the widget
        self.destroyed.connect(self._workers.close_all)
        self.app_box.changed.connect(self._on_app_changed)
        self.tool_box.changed.connect(self._on_tool_changed)

    def showEvent(self, event):  # noqa: N802 - Qt API
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            self._on_tool_changed()  # rebuild once now that a viewer ancestor exists

    # ---- discovery: cached JSON only (no Python subprocess, no scientific imports) ----
    def rescan(self, *_):
        started = time.perf_counter()
        self.schemas, problems = registry.load_schemas()  # one broken app must not hide the others
        self.apps = {name: entry for name, entry in registry.load_all().items() if name in self.schemas}
        self.timings["discovery_ms"] = (time.perf_counter() - started) * 1000
        self.app_box.choices = sorted(self.apps) or ["(none registered)"]
        self._on_app_changed()
        if problems:
            self.status.setText("⚠ skipped: " + "; ".join("%s (%s)" % problem for problem in problems))

    def _on_app_changed(self, *_):
        schema = self.schemas.get(self.app_box.value)
        self.tool_box.choices = [name for name, _ in _tool_names(schema)] if schema else []
        self._on_tool_changed()

    @property
    def tool(self):
        schema = self.schemas.get(self.app_box.value)
        if not schema:
            return None
        return next((t for name, t in _tool_names(schema) if name == self.tool_box.value), None)

    # ---- form ---------------------------------------------------------
    def _on_tool_changed(self, *_):
        self._remove_form()
        tool = self.tool
        if not tool:
            self.description.setText("")
            return
        started = time.perf_counter()
        self.description.setText(tool.get("description", ""))
        try:
            self.gui = self._make_form(tool)
        except (
            ValueError
        ) as error:  # a schema this host cannot show (e.g. an unknown parameter type): say so, no guessed form
            log.error("napari: cannot build the form for %s: %s", tool.get("id"), error)
            self.status.setText("⚠ this tool cannot be shown: %s" % error)
            return
        self.file_sources = self._add_file_sources(tool)
        self.unset_toggles = self._add_unset_toggles(tool)
        self._apply_presentation(tool)
        self._add_choice_boxes(tool)
        self.gui.native.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.form_holder.insertWidget(0, self.gui.native)
        self._schedule_choices(0)
        for param in tool["inputs"]:
            if param.get("pixel_size_of"):
                self._link_calibration(param["pixel_size_of"], param["name"])
        self.timings["gui_construction_ms"] = (time.perf_counter() - started) * 1000

    def _remove_form(self):
        if self.gui is not None:
            self.form_holder.removeWidget(self.gui.native)
            self.gui.native.setParent(None)
            self.gui = None
            self.file_sources = {}
            self.unset_toggles = {}
            self.advanced_toggle, self._members = None, {}
            self._choice_boxes, self._group_members = {}, {}
            self._choice_seq = {}  # an answer still on its way belongs to the form that is gone

    def _make_form(self, tool):
        from napari.layers import Image, Labels

        def run(**form_values):
            self._launch(form_values)

        run.__signature__ = signature_from_schema(tool, {"image": Image, "labels": Labels})
        run.__name__ = tool["id"]
        return magicgui(run, call_button="Run", persist=False, auto_call=False)

    def _add_file_sources(self, tool):
        """Every image/labels parameter can also come from a file: an 'or file' row under its layer chooser.
        A chosen file wins over the layer (which is greyed out); empty it to go back to the layer."""
        sources = {}
        for param in tool["inputs"]:
            if param["type"] not in ("image", "labels"):
                continue
            layer_widget = self.gui[param["name"]]
            edit = mw.FileEdit(
                mode="r",
                nullable=True,
                label="  or file",
                tooltip="Read the image from a file instead of a layer (leave empty to use the layer above)",
                gui_only=True,
            )
            self.gui.insert(list(self.gui).index(layer_widget) + 1, edit)

            edit.changed.connect(lambda *_: self._refresh_enabled())
            sources[param["name"]] = edit
        return sources

    def _add_unset_toggles(self, tool):
        """A number, text or yes/no that is optional and has no default may be left UNSET (the tool then receives None). These
        widgets cannot show "nothing" (an unchecked box would mean False), so each gets a 'set' checkbox: unchecked = greyed
        out and left out of the request."""
        toggles = {}
        for param in tool["inputs"]:
            if not param.get("nullable") or param["type"] not in (
                "integer",
                "float",
                "string",
                "choice",
                "boolean",
            ):
                continue
            widget = self.gui[param["name"]]
            if param["type"] == "string" and getattr(widget, "value", None) == "None":
                widget.value = ""
            toggle = mw.CheckBox(
                value=False,
                text="set",
                label="  (optional)",
                tooltip="Leave unchecked to not set this value",
                gui_only=True,
            )
            self.gui.insert(list(self.gui).index(widget) + 1, toggle)
            widget.enabled = False
            toggle.changed.connect(lambda *_: self._refresh_enabled())
            toggles[param["name"]] = toggle
        return toggles

    # ---- presentation hints: group headings, advanced settings, enabled_when ----
    def _apply_presentation(self, tool):
        inputs = presentation_order(tool)["inputs"]
        self._members = {
            p["name"]: [
                w
                for w in (
                    self.gui[p["name"]],
                    self.file_sources.get(p["name"]),
                    self.unset_toggles.get(p["name"]),
                )
                if w is not None
            ]
            for p in inputs
        }
        advanced_widgets, previous_group = [], None
        for p in inputs:
            first = self._members[p["name"]][0]
            if p.get("advanced") and self.advanced_toggle is None:
                self.advanced_toggle = mw.CheckBox(
                    value=False, text="Show advanced settings", label="", gui_only=True
                )
                self.gui.insert(list(self.gui).index(first), self.advanced_toggle)
                self.advanced_toggle.changed.connect(
                    lambda shown: [setattr(w, "visible", bool(shown)) for w in advanced_widgets]
                )
            group = p.get("group")
            if group and group != previous_group:
                if p.get("group_collapsed"):
                    heading = self._accordion_heading(group)
                else:
                    heading = mw.Label(value=group, label="")
                    heading.native.setStyleSheet("font-weight: bold; padding-top: 6px;")
                self.gui.insert(list(self.gui).index(first), heading)
                if p.get("advanced"):
                    advanced_widgets.append(heading)
            previous_group = group or previous_group
            if p.get("group_collapsed") and group:
                self._group_members.setdefault(group, []).extend(self._members[p["name"]])
            if p.get("advanced"):
                advanced_widgets.extend(self._members[p["name"]])
        for widget in advanced_widgets:
            widget.visible = False
        for group, widgets in self._group_members.items():  # accordion sections start folded
            self._set_group_open(group, widgets, False)
        for p in inputs:  # a parameter's rule is re-evaluated whenever its controlling parameter changes
            rule = p.get("enabled_when")
            if rule:
                controller = self.gui[rule["param"]]
                controller.changed.connect(lambda *_: self._refresh_enabled())
        self._refresh_enabled()

    # ---- accordion groups (Collapsed) ----
    def _accordion_heading(self, group):
        button = mw.PushButton(text="\u25b8 " + group, label="", gui_only=True)
        button.native.setFlat(True)
        button.native.setStyleSheet("font-weight: bold; text-align: left; padding-top: 6px;")
        button.native.setProperty("lc_group", group)
        button.changed.connect(lambda *_: self._toggle_group(group, button))
        self._group_heading = getattr(self, "_group_heading", {})
        self._group_heading[group] = button
        return button

    def _toggle_group(self, group, button):
        widgets = self._group_members[group]
        self._set_group_open(group, widgets, not all(w.visible for w in widgets))

    def _set_group_open(self, group, widgets, opened):
        for w in widgets:
            w.visible = opened
        button = getattr(self, "_group_heading", {}).get(group)
        if button is not None:
            button.text = ("\u25be " if opened else "\u25b8 ") + group

    # ---- dynamic choices (ChoicesFrom) ----
    def _add_choice_boxes(self, tool):
        """A dropdown beside the text field of a ChoicesFrom parameter. The text field stays the value (what is sent); the
        dropdown, when the source tool could answer, hides it and writes into it. When it cannot answer, the text field shows."""
        self._choice_boxes = {}
        for p in tool["inputs"]:
            src = p.get("choices_from")
            if not src:
                continue
            field = self.gui[p["name"]]
            box = mw.ComboBox(choices=[""], label=p["label"], tooltip=p.get("description", ""), gui_only=True)
            box.visible = False
            self.gui.insert(list(self.gui).index(field) + 1, box)
            box.changed.connect(lambda value, f=field, n=p["name"]: self._choice_picked(n, f, value))
            self._choice_boxes[p["name"]] = box
            for name in src["depends"]:
                self.gui[name].changed.connect(lambda *_: self._schedule_choices(400))

    def _choice_picked(self, name, field, value):
        """Picking an option is the value (the text field behind it) and, for an optional parameter, also 'set'; the blank entry unsets it."""
        field.value = value or ""
        toggle = self.unset_toggles.get(name)
        if toggle is not None:
            toggle.value = bool(value)

    def _schedule_choices(self, delay_ms):
        if self._choice_boxes:
            self._choice_timer.start(delay_ms)

    def _choice_inputs(self, source, depends, tool):
        """The request for the source tool from the current form, or None while a needed value is missing."""
        values = {}
        by_name = {p["name"]: p for p in tool["inputs"]}
        for name in depends:
            value = self._control_value(name)
            if value is None or str(value) in ("", "."):
                return None
            if by_name[name]["type"] == "folder" and not Path(value).is_dir():
                return None
            values[name] = str(value) if isinstance(value, Path) else value
        return values

    def _resolve_choices(self):
        tool = self.tool
        if not tool or self.gui is None or not self._choice_boxes:
            return
        if self.task is not None and not self.task.done.is_set():
            self._schedule_choices(1500)  # the worker is busy with a run; ask again afterwards
            return
        app = self.app_box.value
        for p in tool["inputs"]:
            src = p.get("choices_from")
            if not src or p["name"] not in self._choice_boxes:
                continue
            inputs = self._choice_inputs(src["tool"], src["depends"], tool)
            number = self._choice_seq[p["name"]] = self._choice_seq.get(p["name"], 0) + 1
            if inputs is None:
                self._signals.choices.emit((number, p["name"]), None)
                continue
            threading.Thread(
                target=self._ask_choices, args=(app, src, inputs, number, p["name"]), daemon=True
            ).start()

    def _ask_choices(self, app, src, inputs, number, name):
        options = None
        worker = None
        job_dir = tempfile.mkdtemp(prefix="lcchoices_")
        try:
            worker = self._workers.acquire(app, reuse=True)
            task = worker.task(src["tool"], {**inputs, JOB_DIR_KEY: job_dir})
            task.wait()
            if task.status == "COMPLETE":
                for result in task.outputs["results"]:
                    values = result.get("values", {}) if isinstance(result, dict) else {}
                    if isinstance(values.get(src["field"]), list):
                        options = [str(v) for v in values[src["field"]]]
                        break
        except Exception:  # noqa: BLE001 - never blocks the form: the text field remains
            log.error("napari: could not ask %s for choices", src.get("tool"), exc_info=True)
        finally:
            if worker is not None:
                self._workers.release(app, worker, keep=worker.alive)
            shutil.rmtree(job_dir, ignore_errors=True)
        self._signals.choices.emit((number, name), options)

    def _on_choices(self, key, options):
        number, name = key
        box = self._choice_boxes.get(name) if self.gui is not None else None
        if box is None or number != self._choice_seq.get(name):
            return  # a late answer for another question or another form
        field = self.gui[name]
        if not options:
            box.visible = False
            field.visible = True
            return
        current = field.value or ""
        param = next((p for p in self.tool["inputs"] if p["name"] == name), {})
        # A blank entry means "no answer" (unset for an optional parameter, or when the field is empty). A value the field already
        # holds (its default, or what was typed) stays selectable even when the source tool does not list it: never silently dropped.
        entries = ([""] if param.get("nullable") or not current else []) + ([current] if current and current not in options else []) + options
        box.choices = entries
        box.value = current if current in entries else entries[0]
        field.visible = False
        box.visible = True

    def _control_value(self, name):
        source = self.file_sources.get(name)
        if source is not None and _is_set(source.value):
            return Path(
                source.value
            )  # an image chosen as a file is a value too (EnabledWhen("image") must see it)
        toggle = self.unset_toggles.get(name)
        if toggle is not None and not toggle.value:
            return None  # a nullable parameter that is not "set"
        return self.gui[name].value

    def _refresh_enabled(self):
        """One place decides what is greyed out: a failed enabled_when rule, an unticked 'set', or an image given as a file."""
        tool = self.tool
        if not tool or self.gui is None:
            return
        for p in tool["inputs"]:
            name = p["name"]
            rule = p.get("enabled_when")
            applies = rule is None or rule_satisfied(rule, self._control_value(rule["param"]))
            if name in self.file_sources:  # image/labels: the layer chooser yields to a chosen file
                self.gui[name].enabled = applies and not _is_set(self.file_sources[name].value)
                self.file_sources[name].enabled = applies
            elif name in self.unset_toggles:
                self.unset_toggles[name].enabled = applies
                self.gui[name].enabled = applies and bool(self.unset_toggles[name].value)
            else:
                self.gui[name].enabled = applies

    def _refresh_layer_choices(self, *_):
        for widget in self.gui or []:
            if hasattr(widget, "reset_choices"):
                widget.reset_choices()

    def _link_calibration(self, image_name, pixel_name):
        """Pixel-size field follows the layer chosen for its image - until the user edits it."""
        image, pixel = self.gui[image_name], self.gui[pixel_name]
        auto_value = [pixel.value]

        file_edit = self.file_sources.get(image_name)

        def sync(*_):
            if pixel.value != auto_value[0]:
                return
            if file_edit is not None and _is_set(file_edit.value):  # calibration stored in the file
                yx = microns_yx_from_tiff(file_edit.value)
                if yx is not None:
                    auto_value[0] = pixel.value = yx[1]
                    note = anisotropy_note(yx)
                    if note:
                        self.status.setText(note)
                return
            layer = image.value
            if layer is None:
                return
            yx, assumed = microns_per_pixel_yx(layer)
            if yx is not None:
                auto_value[0] = pixel.value = yx[1]
                self.status.setText(
                    anisotropy_note(yx)
                    or (
                        "calibration assumed to be in µm (layer has no unit)"
                        if assumed
                        else self.status.text()
                    )
                )

        image.changed.connect(sync)
        if file_edit is not None:
            file_edit.changed.connect(sync)
        sync()

    # ---- run ----------------------------------------------------------
    def _launch(self, form_values):
        if (
            self.task is not None and not self.task.done.is_set()
        ):  # a run is in progress: never start a second one
            return
        job_dir = Path(tempfile.mkdtemp(prefix="lcin_"))
        try:
            inputs = self._export_inputs(form_values, job_dir)
        except ValueError as error:  # something the user can fix: say it plainly
            shutil.rmtree(job_dir, ignore_errors=True)
            self.status.setText("⚠ %s" % error)
            return
        except (
            Exception
        ) as error:  # noqa: BLE001 - e.g. the disk is full while saving a layer: clean up, say so, log it
            log.error("napari: cannot prepare the inputs for %s", self.app_box.value, exc_info=True)
            shutil.rmtree(job_dir, ignore_errors=True)
            self._show_failure("could not prepare the inputs: %s" % error, error)
            return
        inputs[JOB_DIR_KEY] = str(
            job_dir / "out"
        )  # host-owned: removed by the host whatever happens to the worker
        self._job_dir = job_dir
        self._request = {k: v for k, v in inputs.items() if k != JOB_DIR_KEY}
        self._run_app, self._run_tool = self.app_box.value, self.tool
        shown_inputs = {
            **form_values,
            **{n: FileInput(Path(e.value)) for n, e in self.file_sources.items() if _is_set(e.value)},
        }
        replace = {o["name"] for o in self._run_tool["outputs"] if o.get("replace")}
        self.message_label.setVisible(False)
        self.presenter = ResultPresenter(self.viewer, self._run_app, shown_inputs, replace, self._docks)
        self._set_running(True)
        self._started = time.perf_counter()
        worker = None
        try:
            worker = self._workers.acquire(self._run_app, reuse=self.reuse_box.isChecked())
            self.worker = worker
            self._stderr_mark = getattr(
                worker.stderr, "total", None
            )  # None with an older labconstrictor-tools: then the whole tail is shown
            task = worker.task(
                self._run_tool["id"],
                inputs,
                on_update=lambda message, fraction: self._signals.progress.emit(message, fraction),
            )
            self.task = task
            threading.Thread(target=lambda: (task.wait(), self._signals.done.emit(task)), daemon=True).start()
        except (
            Exception
        ) as error:  # noqa: BLE001 - e.g. the app's Python is gone, or the worker died at once: undo the start
            log.error("napari: cannot start %s for %s", self._run_tool["id"], self._run_app, exc_info=True)
            if worker is not None:
                self._workers.release(self._run_app, worker, keep=False)
            self.task = None
            shutil.rmtree(job_dir, ignore_errors=True)
            self._set_running(False)
            self._show_failure(str(error), error)

    def _show_failure(self, text, error):
        """A failure that never reached a task: status line plus the Details report."""
        self.status.setText("✖ %s" % text)
        self.last_details = "%s\n\nlog file: %s" % (
            error,
            log.log_path(),
        )  # the file holds the full story; its tail may be of other runs
        self.details_button.setEnabled(True)

    def _export_inputs(self, form_values, job_dir):
        """Form values -> worker inputs (layers are saved as TIFF; calibration travels as explicit parameters)."""
        from tifffile import imwrite

        inputs = {}
        for param in self.tool["inputs"]:
            value = form_values.get(param["name"])
            source = self.file_sources.get(param["name"])
            if source is not None and _is_set(source.value):  # file chosen: the worker reads it directly
                path = Path(source.value)
                if not path.is_file():
                    raise ValueError("'%s': file not found: %s" % (param["label"], path))
                inputs[param["name"]] = str(path)
                continue
            toggle = self.unset_toggles.get(param["name"])
            if toggle is not None and not toggle.value:  # nullable and not set: omit it
                continue
            if value is None or (isinstance(value, Path) and str(value) in ("", ".")):
                if param["required"]:
                    raise ValueError("'%s' is required" % param["label"])
                continue
            if param["type"] in ("image", "labels"):
                path = job_dir / (param["name"] + ".tif")
                data = (
                    value.data[0] if getattr(value, "multiscale", False) else value.data
                )  # multiscale: full resolution
                size = int(np.prod(data.shape)) * np.dtype(data.dtype).itemsize
                if size > MAX_EXPORT_BYTES:
                    raise ValueError(
                        "layer '%s' is %.1f GB; exporting more than %.0f GB is not supported"
                        % (value.name, size / 1024**3, MAX_EXPORT_BYTES / 1024**3)
                    )
                imwrite(path, np.asarray(data))
                inputs[param["name"]] = str(path)
            elif param["type"] in ("table", "file", "folder"):
                inputs[param["name"]] = str(value)
            else:
                inputs[param["name"]] = value
        return inputs

    def _set_running(self, running):
        self.bar.setVisible(running)
        if running:
            self.bar.setRange(0, 0)
            self.status.setText("starting worker…")
        self.cancel_button.setEnabled(running)
        self.app_box.enabled = self.tool_box.enabled = (
            not running
        )  # the form must stay the one that was launched
        self.rescan_button.setEnabled(not running)  # a rescan rebuilds the form under a running task
        self.restart_button.setEnabled(not running)  # and a restart would close the worker that is running it
        if self.gui is not None:
            self.gui.call_button.enabled = not running

    def cancel(self, *_):
        if not self.task or self.task.done.is_set():
            return
        self.status.setText("cancelling…")
        task, worker = self.task, self.worker
        task.cancel()
        # Escalate to killing the worker if the tool ignores Cancel. Bound to THIS task, so a stale timer
        # from an earlier run can never kill a later run's worker.
        QTimer.singleShot(int(CANCEL_GRACE_S * 1000), lambda: None if task.done.is_set() else worker.kill())

    def _on_progress(self, message, fraction):
        if fraction is None:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(100 * fraction))
        self.status.setText(message or "")

    def _on_done(self, task):
        if (
            task is not self.task
        ):  # a late signal of a run that is no longer the current one: never touch the current run's files
            return
        self.last_task = task
        self.timings["run_wall_s"] = time.perf_counter() - self._started
        self._set_running(False)
        self._record_run(task)
        healthy = task.status in (
            "COMPLETE",
            "FAILED",
        )  # a cancelled or crashed worker may be mid-task: never reuse it
        self._workers.release(self._run_app, self.worker, keep=self.reuse_box.isChecked() and healthy)
        if task.status != "COMPLETE":
            shutil.rmtree(self._job_dir, ignore_errors=True)
            first_line = (task.error or "").splitlines()[0] if task.error else task.status
            if task.status == "FAILED" and getattr(task, "code", None) in _NO_RESULT_CODES:
                text = "⚠ " + first_line.split("] ", 1)[-1]
            elif task.status == "CRASHED" and getattr(task, "cancel_requested", False):
                text = "cancelled (worker stopped)"
            elif task.status == "CRASHED":
                text = "✖ %s  - click Details… for the full report (log: %s)" % (first_line, log.log_path())
            else:
                text = _FINISH_TEXT.get(
                    task.status,
                    "✖ %s  - click Details… for the full report (log: %s)" % (first_line, log.log_path()),
                )
            self.status.setText(text)
            return
        try:
            summaries = [self.presenter.show(result) for result in task.outputs["results"]]
        except Exception as error:  # noqa: BLE001 - some results may already be shown: do not claim success
            log.error("napari: could not show the results of %s", self._run_tool["id"], exc_info=True)
            self._show_failure(
                "the tool finished but its results could not be shown completely: %s" % error, error
            )
            return
        finally:
            shutil.rmtree(
                self._job_dir, ignore_errors=True
            )  # inputs and outputs share the job dir; results are in the viewer
        if self.presenter.messages:
            self.message_label.setText("\n\n".join(self.presenter.messages))
            self.message_label.setVisible(True)
        failed = [s for s in summaries if s and s.startswith(COULD_NOT_DISPLAY)]
        text = "  ".join(s for s in summaries if s)
        if failed:  # the tool succeeded but part of its output is not in the viewer: not a plain success
            self.status.setText(
                "⚠ done in %.1fs, but some results could not be shown  %s"
                % (self.timings["run_wall_s"], text)
            )
            self.last_details += "\n\nnot shown:\n" + "\n".join(failed)
        else:
            self.status.setText("✔ done in %.1fs  %s" % (self.timings["run_wall_s"], text))
        self._clear_after_run()
        self._schedule_choices(200)  # a run may have changed what the source tool answers (e.g. a game was prepared)

    def _clear_after_run(self):
        """ClearAfterRun parameters go back to their default (or unset) once a run has succeeded."""
        tool = self._run_tool
        if self.gui is None or tool is not self.tool:
            return
        for p in tool["inputs"]:
            if not p.get("clear_after_run"):
                continue
            widget = self.gui[p["name"]]
            toggle = self.unset_toggles.get(p["name"])
            if toggle is not None:
                toggle.value = False
            default = p.get("default", "" if p["type"] == "string" else None)
            if default is not None:
                widget.value = default
            box = self._choice_boxes.get(p["name"])
            if box is not None:
                box.value = ""

    def _record_run(self, task):
        stderr = "".join(self.worker.stderr)
        total = getattr(self.worker.stderr, "total", None)
        if (
            total is not None and self._stderr_mark is not None
        ):  # a kept worker has output of earlier runs: not part of this one
            fresh = total - self._stderr_mark
            stderr = stderr[-fresh:] if fresh > 0 else ""
        self.last_record = runs.record(
            self._run_app, self._run_tool["id"], self._request, task, self.timings["run_wall_s"], stderr
        )
        lines = [
            "status: %s" % task.status,
            "run record: %s" % (self.last_record or "(could not be written)"),
        ]
        if task.error:
            lines += ["", "error: %s" % task.error]
        if task.traceback:
            lines += ["", task.traceback]
        if stderr.strip():
            lines += ["", "worker output (tail):", stderr[-3000:]]
        lines += ["", "log file: %s" % log.log_path()]  # not its tail: that would show earlier runs
        self.last_details = "\n".join(lines)
        self.details_button.setEnabled(True)

    def _show_details(self, *_):
        from qtpy.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle("LabConstrictor - last run")
        box.setText("Run details (copy this when asking for help)")
        box.setDetailedText(self.last_details)
        box.exec_()

    def closeEvent(self, event):  # noqa: N802 - Qt API
        self._reaper.stop()
        if (
            self.task is not None and not self.task.done.is_set()
        ):  # closed during a run: stop it, do not leave worker or temp files
            try:
                self.worker.kill()
            except Exception:  # noqa: BLE001 - best effort while closing, but leave a trace
                log.error("napari: could not stop the worker while closing the widget", exc_info=True)
            shutil.rmtree(getattr(self, "_job_dir", ""), ignore_errors=True)
        self._workers.close_all()
        super().closeEvent(event)

    @property
    def tables(self):
        return self.presenter.tables if self.presenter else {}
