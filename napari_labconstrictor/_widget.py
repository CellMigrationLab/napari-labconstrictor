"""ONE napari dock widget for every installed LabConstrictor app.

registry (cached JSON) -> app/tool choosers -> schema -> synthesized signature -> magicgui form -> worker -> typed results
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from labconstrictor_tools import log, registry, runs
from labconstrictor_tools.protocol import JOB_DIR_KEY
from magicgui import magicgui
from magicgui import widgets as mw
from magicgui.types import FileDialogMode
from qtpy.QtCore import QObject, Qt, QTimer, Signal
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ._export import channel_file, export_layer, selection_mask
from ._export import is_set as _is_set
from ._results import COULD_NOT_DISPLAY, FileInput, ResultPresenter
from ._schema import Param, Tool, presentation_order, rule_satisfied, signature_from_schema
from ._units import (
    TIFF_READ_ERRORS,
    anisotropy_note,
    microns_per_pixel_yx,
    microns_yx_from_tiff_checked,
)
from ._workers import WorkerCache

if TYPE_CHECKING:  # annotation only; at runtime napari passes a proxy under the name `napari_viewer`
    from napari import Viewer

CANCEL_GRACE_S = 3.0  # how long a tool gets to honour Cancel before its worker is killed
REAP_INTERVAL_MS = 30_000  # how often idle workers are looked for (see WorkerCache.reap_idle)
CHOICES_DEBOUNCE_MS = 400  # wait after a controlling value changes before asking for new ChoicesFrom options
CHOICES_RETRY_MS = 1500  # a run is using the worker: ask again after this long
CHOICES_AFTER_RUN_MS = 200  # a finished run may have changed what the source tool answers: ask again
FORM_MIN_HEIGHT_PX = 180  # the form area never shrinks below this, however small the dock
OUTCOME_MIN_HEIGHT_PX = 110  # the status/message box: at least a few lines ...
OUTCOME_MAX_HEIGHT_PX = 320  # ... and at most this tall (longer text scrolls) so it never squeezes the form
STDERR_TAIL_CHARS = 3000  # worker output kept in the Details report
_FINISH_TEXT = {"CANCELED": "cancelled"}
_OMIT = object()  # "leave this parameter out" (None is a real value)
_NO_RESULT_CODES = ("no_match", "no_result")  # an outcome ("nothing found"), not a fault: shown as a notice


def _tool_names(schema: dict[str, Any]) -> list[tuple[str, Tool]]:
    """[(name shown in the chooser, tool)]: the label, or `label (id)` when two tools share a label (never pick the wrong one)."""
    labels = [t["label"] for t in schema["tools"]]
    return [
        (t["label"] if labels.count(t["label"]) == 1 else "%s (%s)" % (t["label"], t["id"]), t)
        for t in schema["tools"]
    ]


def _failure_line(first_line: str) -> str:
    return "✖ %s  - click Details… for the full report (log: %s)" % (first_line, log.log_path())


def _unfinished_text(task: Any) -> str:
    """The status line for a task that did not complete (failed, cancelled or crashed)."""
    first_line = (task.error or "").splitlines()[0] if task.error else task.status
    if task.status == "FAILED" and getattr(task, "code", None) in _NO_RESULT_CODES:
        return "⚠ " + first_line.split("] ", 1)[-1]
    if task.status == "CRASHED" and getattr(task, "cancel_requested", False):
        return "cancelled (worker stopped)"
    if task.status == "CRASHED" or task.status not in _FINISH_TEXT:
        return _failure_line(first_line)
    return _FINISH_TEXT[task.status]


class _Signals(QObject):
    progress = Signal(object, object)
    done = Signal(object)
    choices = Signal(object, object)  # (request number, list of options or None)


# `cast(Tool, ...)` below: `tool` is None only before a tool is chosen, and these methods run only from a built form;
# the cast is a no-op at run time (the behaviour on None is unchanged).


class LabConstrictorWidget(QWidget):
    """The one dock widget: owns the form of the chosen tool and the state of the run in progress (task, worker, job folder).

    Qt and magicgui objects are typed `Any` below because neither ships stubs mypy can use; the schema dicts are `Tool`/`Param`.
    """

    def __init__(self, napari_viewer: Viewer | None = None) -> None:
        super().__init__()
        self.viewer = napari_viewer
        self._init_state()
        self._workers = WorkerCache()
        self._signals = _Signals()
        self._signals.progress.connect(self._on_progress)
        self._signals.done.connect(self._on_done)
        self._signals.choices.connect(self._on_choices)
        self._choice_timer = QTimer(self)
        self._choice_timer.setSingleShot(True)
        self._choice_timer.timeout.connect(self._resolve_choices)
        self._build_layout()
        if self.viewer is not None:
            # Layer choosers must follow the layer list even if this widget was built before it was docked.
            self.viewer.layers.events.inserted.connect(self._refresh_layer_choices)
            self.viewer.layers.events.removed.connect(self._refresh_layer_choices)
        self.rescan()

    def _init_state(self) -> None:
        # `Any` below: magicgui/Qt widgets and the Task/WorkerProcess objects of labconstrictor_tools have no stubs, and
        # the run-state attributes are None until a run starts (then set together by _begin_run / _start_worker)
        self.apps: dict[str, Any] = {}
        self.schemas: dict[str, Any] = {}
        self.gui: Any = None  # the magicgui form of the current tool
        self.file_sources: dict[str, Any] = {}  # image parameter -> "or file" widget
        self.unset_toggles: dict[str, Any] = {}  # nullable parameter -> "set" checkbox
        self.region_toggles: dict[str, Any] = {}  # RegionOf parameter -> "use the selection" checkbox
        self.channel_boxes: dict[str, Any] = {}  # PickChannel image parameter -> its channel chooser
        self.advanced_toggle: Any = (
            None  # "Show advanced settings" checkbox (only when a tool has advanced parameters)
        )
        self._members: dict[str, list[Any]] = (
            {}
        )  # parameter -> its widgets (value widget, "or file" row, "set" box)
        self.task: Any = None
        self.worker: Any = None
        self.last_task: Any = None
        self.presenter: Any = None  # a ResultPresenter once a run has started
        self.last_record: Any = None
        self.last_details = ""
        self._request: dict[str, Any] = {}
        # what the running task was started with (the choosers may change meanwhile)
        self._run_app: Any = None
        self._run_tool: Any = None
        self.timings: dict[str, float] = {}
        self._choice_problems: dict[tuple[int, str], str] = (
            {}
        )  # (request number, parameter) -> why no choices
        self._choice_boxes: dict[str, Any] = {}  # ChoicesFrom parameter -> its dropdown
        self._choice_seq: dict[str, int] = (
            {}
        )  # parameter -> number of the newest question; older answers are dropped
        self._docks: dict[tuple[str, str], Any] = (
            {}
        )  # (app, table name) -> dock widget, so that Replace can swap it
        self._group_members: dict[str, list[Any]] = {}  # collapsible group -> widgets

    # ---- layout -------------------------------------------------------
    def _build_layout(self) -> None:
        self._create_controls()
        layout = QVBoxLayout(self)
        for widget in (self.app_box.native, self.tool_box.native, self.description):
            layout.addWidget(widget)
        layout.addWidget(self._build_form_area(), 1)
        layout.addWidget(self.bar)
        layout.addWidget(self._build_outcome_area(), 1)
        layout.addWidget(self.reuse_box)
        layout.addLayout(self._button_row())
        self._connect_controls()

    def _create_controls(self) -> None:
        self.app_box = mw.ComboBox(label="Application")
        self.tool_box = mw.ComboBox(label="Tool")
        self.description = QLabel("")
        self.description.setWordWrap(True)
        self.form_holder = QVBoxLayout()
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        self.status = QLabel("idle")
        self.status.setWordWrap(True)
        self.message_label = QLabel(
            ""
        )  # message results of the last run (markdown); hidden when there is none
        self.message_label.setWordWrap(True)
        self.message_label.setTextFormat(Qt.TextFormat.MarkdownText)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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
        self.copy_button = QPushButton("Copy as command")
        self.copy_button.setToolTip(
            "Copy what repeats this run outside Napari: a terminal line or a Python snippet"
        )
        copy_menu = QMenu(self.copy_button)
        for label, kind in (("Terminal command", "terminal"), ("Python snippet", "python")):
            action = copy_menu.addAction(label)
            if action is None:  # Qt's stubs allow it; addAction(str) never returns None in practice
                raise RuntimeError("Qt could not create the menu entry %r" % label)
            action.triggered.connect(self._copy_handler(kind))
        self.copy_button.setMenu(copy_menu)

    def _copy_handler(self, kind: str) -> Callable[..., None]:
        """A menu-entry slot that copies as `kind` (a slot returns nothing; the copied text is for tests/callers)."""

        def copy(*_: object) -> None:
            self.copy_as_command(kind)

        return copy

    def _button_row(self) -> QHBoxLayout:
        buttons = QHBoxLayout()
        for button in (
            self.cancel_button,
            self.rescan_button,
            self.restart_button,
            self.details_button,
            self.copy_button,
        ):
            buttons.addWidget(button)
        return buttons

    def _build_form_area(self) -> QScrollArea:
        """The form can be taller than the dock (advanced settings of a big tool): it scrolls, while the progress bar, status and
        buttons below stay in view."""
        form_container = QWidget()
        form_container.setLayout(self.form_holder)
        self.form_holder.setContentsMargins(0, 0, 0, 0)
        self.form_holder.addStretch(1)  # a short form sits at the top instead of being spread over the dock
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QFrame.NoFrame)
        self.form_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )  # a form wider than the dock scrolls sideways: nothing is clipped out of reach
        self.form_scroll.setMinimumHeight(FORM_MIN_HEIGHT_PX)
        self.form_scroll.setWidget(form_container)
        return self.form_scroll

    def _build_outcome_area(self) -> QScrollArea:
        """Status and message can be long (a readout, many values): they scroll in a box of limited height, so they never
        squeeze the form."""
        outcome = QWidget()
        outcome_layout = QVBoxLayout(outcome)
        outcome_layout.setContentsMargins(0, 0, 0, 0)
        outcome_layout.addWidget(self.message_label)  # the readout first: it is what the person came for
        outcome_layout.addWidget(self.status)
        outcome_layout.addStretch(1)
        self.outcome_scroll = QScrollArea()
        self.outcome_scroll.setWidgetResizable(True)
        self.outcome_scroll.setFrameShape(QFrame.NoFrame)
        self.outcome_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.outcome_scroll.setMaximumHeight(OUTCOME_MAX_HEIGHT_PX)
        self.outcome_scroll.setMinimumHeight(OUTCOME_MIN_HEIGHT_PX)
        self.outcome_scroll.setWidget(outcome)
        return self.outcome_scroll

    def _connect_controls(self) -> None:
        self.cancel_button.clicked.connect(self.cancel)
        self.details_button.clicked.connect(self._show_details)
        self.rescan_button.clicked.connect(self.rescan)
        self.restart_button.clicked.connect(lambda *_: self._workers.discard(self.app_box.value))
        self._stderr_mark: int | None = 0  # how much worker output existed when the current run started
        self._reaper = QTimer(self)
        self._reaper.timeout.connect(self._workers.reap_idle)
        self._reaper.start(REAP_INTERVAL_MS)
        # closing the Napari window deletes this widget without calling closeEvent: stop the workers then too (not only when the
        # whole Python process ends); bound to the cache object, which outlives the widget
        self.destroyed.connect(self._workers.close_all)
        self.app_box.changed.connect(self._on_app_changed)
        self.tool_box.changed.connect(self._on_tool_changed)

    def showEvent(self, event: Any) -> None:  # Qt API name
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            self._on_tool_changed()  # rebuild once now that a viewer ancestor exists

    # ---- discovery: cached JSON only (no Python subprocess, no scientific imports) ----
    def rescan(self, *_: Any) -> None:
        started = time.perf_counter()
        self.schemas, problems = registry.load_schemas()  # one broken app must not hide the others
        self.apps = {name: entry for name, entry in registry.load_all().items() if name in self.schemas}
        self.timings["discovery_ms"] = (time.perf_counter() - started) * 1000
        self.app_box.choices = sorted(self.apps) or ["(none registered)"]
        self._on_app_changed()
        if problems:
            self.status.setText("⚠ skipped: " + "; ".join("%s (%s)" % problem for problem in problems))

    def _on_app_changed(self, *_: Any) -> None:
        schema = self.schemas.get(self.app_box.value)
        self.tool_box.choices = [name for name, _ in _tool_names(schema)] if schema else []
        self._on_tool_changed()

    @property
    def tool(self) -> Tool | None:
        schema = self.schemas.get(self.app_box.value)
        if not schema:
            return None
        return next((t for name, t in _tool_names(schema) if name == self.tool_box.value), None)

    # ---- form ---------------------------------------------------------
    def _on_tool_changed(self, *_: Any) -> None:
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
        self.channel_boxes = self._add_channel_choosers(tool)
        self.unset_toggles = self._add_unset_toggles(tool)
        self.region_toggles = self._add_region_toggles(tool)
        self._apply_presentation(tool)
        self._add_choice_boxes(tool)
        self.gui.native.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.form_holder.insertWidget(0, self.gui.native)
        self._schedule_choices(0)
        for param in tool["inputs"]:
            if param.get("pixel_size_of"):
                self._link_calibration(param["pixel_size_of"], param["name"])
        self.timings["gui_construction_ms"] = (time.perf_counter() - started) * 1000

    def _remove_form(self) -> None:
        if self.gui is not None:
            self.form_holder.removeWidget(self.gui.native)
            self.gui.native.setParent(None)
            self.gui = None
            self.file_sources = {}
            self.unset_toggles = {}
            self.region_toggles = {}
            self.channel_boxes = {}
            self.advanced_toggle, self._members = None, {}
            self._choice_boxes, self._group_members = {}, {}
            self._choice_seq = {}  # an answer still on its way belongs to the form that is gone

    def _make_form(self, tool: Tool) -> Any:
        from napari.layers import Image, Labels

        def run(**form_values: Any) -> None:
            self._launch(form_values)

        signature = signature_from_schema(tool, {"image": Image, "labels": Labels})
        run.__signature__ = signature  # type: ignore[attr-defined]  # typeshed omits __signature__ on functions; magicgui reads it
        run.__name__ = tool["id"]
        return magicgui(run, call_button="Run", persist=False, auto_call=False)

    def _add_file_sources(self, tool: Tool) -> dict[str, Any]:
        """Every image/labels parameter can also come from a file: an 'or file' row under its layer chooser.
        A chosen file wins over the layer (which is greyed out); empty it to go back to the layer."""
        sources = {}
        for param in tool["inputs"]:
            if param["type"] not in ("image", "labels"):
                continue
            layer_widget = self.gui[param["name"]]
            edit = mw.FileEdit(
                mode=FileDialogMode.EXISTING_FILE,  # "r"
                nullable=True,
                label="  or file",
                tooltip="Read the image from a file instead of a layer (leave empty to use the layer above)",
                gui_only=True,
            )
            self.gui.insert(list(self.gui).index(layer_widget) + 1, edit)

            edit.changed.connect(lambda *_: self._refresh_enabled())
            sources[param["name"]] = edit
        return sources

    def _add_channel_choosers(self, tool: Tool) -> dict[str, Any]:
        """PickChannel: a Channel chooser under the image. It lists the channels it can see (the colours of an RGB layer, the C axis of a
        TIFF given as a file) and is hidden when there is only one; the tool then receives just the chosen channel.
        """
        boxes = {}
        for param in tool["inputs"]:
            if not param.get("pick_channel") or param["name"] not in self.file_sources:
                continue
            name = param["name"]
            box = mw.ComboBox(
                choices=[""], label="  channel", tooltip="The tool receives only this channel", gui_only=True
            )
            box.visible = False
            edit = self.file_sources[name]
            self.gui.insert(list(self.gui).index(edit) + 1, box)
            boxes[name] = box
            self.gui[name].changed.connect(lambda *_, n=name: self._refresh_channels(n))
            edit.changed.connect(lambda *_, n=name: self._refresh_channels(n))
            self.channel_boxes[name] = box
            self._refresh_channels(name)
        return boxes

    def _channel_names(self, name: str) -> list[str]:
        """The channels of what is chosen for image parameter `name`: names, or [] when the image is a single channel."""
        source = self.file_sources.get(name)
        if source is not None and _is_set(source.value):
            import tifffile

            try:
                with tifffile.TiffFile(str(source.value)) as tif:
                    series = tif.series[0]
                    if "C" in series.axes:
                        return ["Channel %d" % (i + 1) for i in range(series.shape[series.axes.index("C")])]
            except (
                *TIFF_READ_ERRORS,
                tifffile.TiffFileError,
            ) as error:  # unreadable: no channels to choose from
                log.warning(
                    "napari: cannot read the channels of %s (%s: %s)",
                    source.value,
                    type(error).__name__,
                    error,
                )
                self.status.setText("⚠ could not read the channels of %s: %s" % (source.value, error))
            return []
        layer = self.gui[name].value
        if layer is not None and getattr(layer, "rgb", False):
            return ["Red", "Green", "Blue"]
        return []

    def _refresh_channels(self, name: str) -> None:
        box = self.channel_boxes.get(name)
        if box is None:
            return
        names = self._channel_names(name)
        box.choices = names or [""]
        box.value = names[0] if names else ""
        box.visible = len(names) > 1

    def _add_unset_toggles(self, tool: Tool) -> dict[str, Any]:
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

    def _add_region_toggles(self, tool: Tool) -> dict[str, Any]:
        """RegionOf: an optional Labels input that the host fills from the selection. A 'use the selection' box (off by default)
        decides whether the selected Shapes layer is sent as the region; when on it takes the place of the layer chooser.
        """
        toggles = {}
        for param in tool["inputs"]:
            if not param.get("region_of"):
                continue
            toggle = mw.CheckBox(
                value=False,
                text="use the selection",
                label="  (region)",
                tooltip="Send the shapes of the selected Shapes layer as the region (several shapes are labels 1, 2, 3...)",
                gui_only=True,
            )
            self.gui.insert(list(self.gui).index(self.gui[param["name"]]) + 1, toggle)
            toggle.changed.connect(lambda *_: self._refresh_enabled())
            toggles[param["name"]] = toggle
        return toggles

    def _selection_mask(self, param: Param, form_values: dict[str, Any], job_dir: Path) -> str:
        """The shapes of the selected Shapes layer as a label image (see `_export.selection_mask`)."""
        return selection_mask(
            self.viewer,
            param,
            self.file_sources.get(param["region_of"]),
            form_values.get(param["region_of"]),
            job_dir,
            image_label=self._label_of(param["region_of"]),
        )

    def _label_of(self, name: str) -> str:
        """The label the person sees for parameter `name` of the current tool."""
        return next((p["label"] for p in cast(Tool, self.tool)["inputs"] if p["name"] == name), name)

    # ---- presentation hints: group headings, advanced settings, enabled_when ----
    def _apply_presentation(self, tool: Tool) -> None:
        inputs = presentation_order(tool)["inputs"]
        self._members = {
            p["name"]: [
                w
                for w in (
                    self.gui[p["name"]],
                    self.file_sources.get(p["name"]),
                    self.channel_boxes.get(p["name"]),
                    self.unset_toggles.get(p["name"]),
                )
                if w is not None
            ]
            for p in inputs
        }
        advanced_widgets = self._insert_headings(inputs)
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

    def _insert_headings(self, inputs: list[Param]) -> list[Any]:
        """Put the group headings (and the 'Show advanced settings' box) into the form; returns the advanced widgets."""
        advanced_widgets: list[Any] = []
        previous_group = None
        for p in inputs:
            first = self._members[p["name"]][0]
            if p.get("advanced") and self.advanced_toggle is None:
                self._add_advanced_toggle(first, advanced_widgets)
            group = p.get("group")
            if group and group != previous_group:
                heading = self._group_heading_widget(group, p.get("group_collapsed"))
                self.gui.insert(list(self.gui).index(first), heading)
                if p.get("advanced"):
                    advanced_widgets.append(heading)
            previous_group = group or previous_group
            if p.get("group_collapsed") and group:
                self._group_members.setdefault(group, []).extend(self._members[p["name"]])
            if p.get("advanced"):
                advanced_widgets.extend(self._members[p["name"]])
        return advanced_widgets

    def _add_advanced_toggle(self, first: Any, advanced_widgets: list[Any]) -> None:
        self.advanced_toggle = mw.CheckBox(
            value=False, text="Show advanced settings", label="", gui_only=True
        )
        self.gui.insert(list(self.gui).index(first), self.advanced_toggle)

        def show_advanced(shown: Any) -> None:
            for w in advanced_widgets:
                w.visible = bool(shown)

        self.advanced_toggle.changed.connect(show_advanced)

    def _group_heading_widget(self, group: str, collapsed: Any) -> Any:
        if collapsed:
            return self._accordion_heading(group)
        heading = mw.Label(value=group, label="")
        heading.native.setStyleSheet("font-weight: bold; padding-top: 6px;")
        return heading

    # ---- accordion groups (Collapsed) ----
    def _accordion_heading(self, group: str) -> Any:
        button = mw.PushButton(text="\u25b8 " + group, label="", gui_only=True)
        button.native.setFlat(True)
        button.native.setStyleSheet("font-weight: bold; text-align: left; padding-top: 6px;")
        button.native.setProperty("lc_group", group)
        button.changed.connect(lambda *_: self._toggle_group(group, button))
        self._group_heading = getattr(self, "_group_heading", {})
        self._group_heading[group] = button
        return button

    def _toggle_group(self, group: str, button: Any) -> None:
        widgets = self._group_members[group]
        self._set_group_open(group, widgets, not all(w.visible for w in widgets))

    def _set_group_open(self, group: str, widgets: list[Any], opened: bool) -> None:
        for w in widgets:
            w.visible = opened
        button = getattr(self, "_group_heading", {}).get(group)
        if button is not None:
            button.text = ("\u25be " if opened else "\u25b8 ") + group

    # ---- dynamic choices (ChoicesFrom) ----
    def _add_choice_boxes(self, tool: Tool) -> None:
        """A dropdown beside the text field of a ChoicesFrom parameter. The text field stays the value (what is sent); the
        dropdown, when the source tool could answer, hides it and writes into it. When it cannot answer, the text field shows.
        """
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
                self.gui[name].changed.connect(lambda *_: self._schedule_choices(CHOICES_DEBOUNCE_MS))

    def _choice_picked(self, name: str, field: Any, value: str | None) -> None:
        """Picking an option is the value (the text field behind it) and, for an optional parameter, also 'set'; the blank entry unsets it."""
        field.value = value or ""
        toggle = self.unset_toggles.get(name)
        if toggle is not None:
            toggle.value = bool(value)

    def _schedule_choices(self, delay_ms: int) -> None:
        if self._choice_boxes:
            self._choice_timer.start(delay_ms)

    def _choice_inputs(self, source: str, depends: list[str], tool: Tool) -> dict[str, Any] | None:
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

    def _resolve_choices(self) -> None:
        tool = self.tool
        if not tool or self.gui is None or not self._choice_boxes:
            return
        if self.task is not None and not self.task.done.is_set():
            self._schedule_choices(CHOICES_RETRY_MS)  # the worker is busy with a run; ask again afterwards
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

    def _ask_choices(
        self, app: str, src: dict[str, Any], inputs: dict[str, Any], number: int, name: str
    ) -> None:
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
            if options is None:  # intended fallback (the text field stays), but never a silent one
                log.warning(
                    "napari: %s gave no list for %r (status %s); using a text field",
                    src.get("tool"),
                    src["field"],
                    task.status,
                )
                self._choice_problems[(number, name)] = "%s gave no choices for %s" % (src.get("tool"), name)
        # BLE001: isolation boundary - any worker failure must leave the text field usable
        except Exception:  # noqa: BLE001
            log.error("napari: could not ask %s for choices", src.get("tool"), exc_info=True)
            self._choice_problems[(number, name)] = "could not get the choices for %s (see the log)" % name
        finally:
            if worker is not None:
                self._workers.release(app, worker, keep=worker.alive)
            shutil.rmtree(job_dir, ignore_errors=True)
        self._signals.choices.emit((number, name), options)

    def _on_choices(self, key: tuple[int, str], options: list[str] | None) -> None:
        number, name = key
        box = self._choice_boxes.get(name) if self.gui is not None else None
        if box is None or number != self._choice_seq.get(name):
            return  # a late answer for another question or another form
        field = self.gui[name]
        if not options:
            box.visible = False
            field.visible = True
            problem = self._choice_problems.pop(key, None)
            if problem:  # the text field stays, and the person is told why there is no list
                text = "%s: type the value" % problem
                tip = field.native.toolTip()
                if text not in tip:
                    field.native.setToolTip((tip + "\n" if tip else "") + text)
                if not self.status.text():  # never overwrite the outcome of a run with a side question
                    self.status.setText("⚠ " + text)
            return
        current = field.value or ""
        param: Param = next((p for p in cast(Tool, self.tool)["inputs"] if p["name"] == name), {})
        # A blank entry means "no answer" (unset for an optional parameter, or when the field is empty). A value the field already
        # holds (its default, or what was typed) stays selectable even when the source tool does not list it: never silently dropped.
        entries = (
            ([""] if param.get("nullable") or not current else [])
            + ([current] if current and current not in options else [])
            + options
        )
        box.choices = entries
        box.value = current if current in entries else entries[0]
        field.visible = False
        box.visible = True

    def _control_value(self, name: str) -> Any:
        source = self.file_sources.get(name)
        if source is not None and _is_set(source.value):
            return Path(
                source.value
            )  # an image chosen as a file is a value too (EnabledWhen("image") must see it)
        toggle = self.unset_toggles.get(name)
        if toggle is not None and not toggle.value:
            return None  # a nullable parameter that is not "set"
        return self.gui[name].value

    def _refresh_enabled(self) -> None:
        """One place decides what is greyed out: a failed enabled_when rule, an unticked 'set', or an image given as a file."""
        tool = self.tool
        if not tool or self.gui is None:
            return
        for p in tool["inputs"]:
            name = p["name"]
            rule = p.get("enabled_when")
            applies = rule is None or rule_satisfied(rule, self._control_value(rule["param"]))
            region_on = name in self.region_toggles and bool(self.region_toggles[name].value)
            if name in self.region_toggles:
                self.region_toggles[name].enabled = applies
            if (
                name in self.file_sources
            ):  # image/labels: the layer chooser yields to a chosen file (or to the selection)
                self.gui[name].enabled = (
                    applies and not region_on and not _is_set(self.file_sources[name].value)
                )
                self.file_sources[name].enabled = applies and not region_on
            elif name in self.unset_toggles:
                self.unset_toggles[name].enabled = applies
                self.gui[name].enabled = applies and bool(self.unset_toggles[name].value)
            else:
                self.gui[name].enabled = applies

    def _refresh_layer_choices(self, *_: Any) -> None:
        for widget in self.gui or []:
            if hasattr(widget, "reset_choices"):
                widget.reset_choices()

    def _link_calibration(self, image_name: str, pixel_name: str) -> None:
        """Pixel-size field follows the layer chosen for its image - until the user edits it."""
        image, pixel = self.gui[image_name], self.gui[pixel_name]
        auto_value = [pixel.value]
        file_edit = self.file_sources.get(image_name)

        def sync(*_):
            if pixel.value != auto_value[0]:
                return
            yx = self._detect_calibration(image, file_edit)
            if yx is not None:
                auto_value[0] = pixel.value = yx[1]

        image.changed.connect(sync)
        if file_edit is not None:
            file_edit.changed.connect(sync)
        sync()

    def _detect_calibration(self, image: Any, file_edit: Any) -> tuple[float, float] | None:
        """(y, x) micrometres per pixel of the chosen file, else of the chosen layer, or None; what it found goes to the status line."""
        if file_edit is not None and _is_set(file_edit.value):  # calibration stored in the file
            yx, problem = microns_yx_from_tiff_checked(file_edit.value)
            if problem:
                self.status.setText("⚠ %s" % problem)
            note = anisotropy_note(yx) if yx is not None else None
            if note:
                self.status.setText(note)
            return yx
        layer = image.value
        if layer is None:
            return None
        yx, assumed = microns_per_pixel_yx(layer)
        if yx is not None:
            self.status.setText(
                anisotropy_note(yx)
                or ("calibration assumed to be in µm (layer has no unit)" if assumed else self.status.text())
            )
        return yx

    # ---- run ----------------------------------------------------------
    def _launch(self, form_values: dict[str, Any]) -> None:
        if (
            self.task is not None and not self.task.done.is_set()
        ):  # a run is in progress: never start a second one
            return
        job_dir = Path(tempfile.mkdtemp(prefix="lcin_"))
        inputs = self._prepare_inputs(form_values, job_dir)
        if inputs is None:
            return
        self._begin_run(form_values, inputs, job_dir)
        self._start_worker(inputs, job_dir)

    def _prepare_inputs(self, form_values: dict[str, Any], job_dir: Path) -> dict[str, Any] | None:
        """The worker inputs, or None after telling the person why they could not be made (the job dir is removed then)."""
        try:
            return self._export_inputs(form_values, job_dir)
        except ValueError as error:  # something the user can fix: say it plainly
            shutil.rmtree(job_dir, ignore_errors=True)
            self.status.setText("⚠ %s" % error)
        # BLE001: e.g. the disk is full while saving a layer - clean up, say so, log it
        except Exception as error:  # noqa: BLE001
            log.error("napari: cannot prepare the inputs for %s", self.app_box.value, exc_info=True)
            shutil.rmtree(job_dir, ignore_errors=True)
            self._show_failure("could not prepare the inputs: %s" % error, error)
        return None

    def _begin_run(self, form_values: dict[str, Any], inputs: dict[str, Any], job_dir: Path) -> None:
        """Remember what this run was started with and get the result presenter and the running state ready."""
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
        images = [p["name"] for p in self._run_tool["inputs"] if p["type"] in ("image", "labels")]
        self.presenter = ResultPresenter(
            self.viewer, self._run_app, shown_inputs, replace, self._docks, image_inputs=images
        )
        self._set_running(True)
        self._started = time.perf_counter()

    def _start_worker(self, inputs: dict[str, Any], job_dir: Path) -> None:
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

            def wait_then_signal() -> None:
                task.wait()
                self._signals.done.emit(task)

            threading.Thread(target=wait_then_signal, daemon=True).start()
        # BLE001: e.g. the app's Python is gone or the worker died at once - undo the start
        except Exception as error:  # noqa: BLE001
            log.error("napari: cannot start %s for %s", self._run_tool["id"], self._run_app, exc_info=True)
            if worker is not None:
                self._workers.release(self._run_app, worker, keep=False)
            self.task = None
            shutil.rmtree(job_dir, ignore_errors=True)
            self._set_running(False)
            self._show_failure(str(error), error)

    def _show_failure(self, text: str, error: BaseException) -> None:
        """A failure that never reached a task: status line plus the Details report."""
        self.status.setText("✖ %s" % text)
        self.last_details = "%s\n\nlog file: %s" % (
            error,
            log.log_path(),
        )  # the file holds the full story; its tail may be of other runs
        self.details_button.setEnabled(True)

    def _export_inputs(self, form_values: dict[str, Any], job_dir: Path) -> dict[str, Any]:
        """Form values -> worker inputs (layers are saved as TIFF; calibration travels as explicit parameters)."""
        inputs = {}
        for param in cast(Tool, self.tool)["inputs"]:
            exported = self._export_param(param, form_values, job_dir)
            if exported is not _OMIT:
                inputs[param["name"]] = exported
        return inputs

    def _export_param(self, param: Param, form_values: dict[str, Any], job_dir: Path) -> Any:
        """The worker input for one parameter, or `_OMIT` when it is left out of the request."""
        name = param["name"]
        value = form_values.get(name)
        source = self.file_sources.get(name)
        region = self.region_toggles.get(name)
        if region is not None and region.value:  # RegionOf: the selection is the value
            return self._selection_mask(param, form_values, job_dir)
        if source is not None and _is_set(source.value):  # file chosen: the worker reads it directly
            return self._export_file_source(param, Path(source.value), job_dir)
        toggle = self.unset_toggles.get(name)
        if toggle is not None and not toggle.value:  # nullable and not set: omit it
            return _OMIT
        if value is None or (isinstance(value, Path) and str(value) in ("", ".")):
            if param["required"]:
                hint = ": open an image or choose a file" if param["type"] in ("image", "labels") else ""
                raise ValueError("[missing_parameter] '%s' is required%s" % (param["label"], hint))
            return _OMIT
        if param["type"] in ("image", "labels"):
            path = job_dir / (name + ".tif")
            export_layer(value, self._picked_channel(name), path)
            return str(path)
        if param["type"] in ("table", "file", "folder"):
            return str(value)
        return value

    def _export_file_source(self, param: Param, path: Path, job_dir: Path) -> str:
        if not path.is_file():
            raise ValueError("[file_not_found] image file not found: %s" % path)
        picked = self._picked_channel(param["name"])
        if picked is not None:  # PickChannel: the file's chosen channel is written for the worker
            path = channel_file(path, picked, param["label"], job_dir / (param["name"] + ".tif"))
        return str(path)

    def current_values(self) -> dict[str, Any]:
        """The form as the values a command line needs: unset optional parameters are omitted; an image or labels input is the
        file it came from (a file chosen in the form, else the layer's source file) or None, which becomes a placeholder.
        """
        values = {}
        for param in cast(Tool, self.tool)["inputs"]:
            value = self._command_value(param)
            if value is not _OMIT:
                values[param["name"]] = value
        return values

    def _command_value(self, param: Param) -> Any:
        """One parameter as a command line needs it, or `_OMIT` when it is left out."""
        name = param["name"]
        toggle = self.unset_toggles.get(name)
        if toggle is not None and not toggle.value:
            return _OMIT
        region = self.region_toggles.get(name)
        if (
            region is not None and region.value
        ):  # a selection cannot be written on a command line: the note says so
            return "region.tif"
        source = self.file_sources.get(name)
        if source is not None and _is_set(source.value):
            return str(source.value)
        value = self.gui[name].value
        if param["type"] in ("image", "labels"):
            path = getattr(getattr(value, "source", None), "path", None)
            return str(path) if path else None
        if value is None or (isinstance(value, Path) and str(value) in ("", ".")):
            return _OMIT
        if param["type"] in ("table", "file", "folder"):
            return str(value)
        return value

    def copy_as_command(self, kind: str = "terminal") -> str | None:
        """Put the terminal line or the Python snippet that repeats this form on the clipboard."""
        from labconstrictor_tools import command

        if self.gui is None or self.tool is None:
            return None
        app = self.app_box.value
        values = self.current_values()
        if kind == "python":
            text = command.python_snippet(app, self.tool, values)
        else:
            text = command.command_line(app, self.tool, values, python=self.apps[app].get("python", "python"))
        notes = self._command_notes()
        if (
            notes
        ):  # as comment lines after the placeholder line (if any), before the command: the order every host uses
            lines = text.split("\n")
            head = next((i for i, line in enumerate(lines) if not line.startswith("#")), len(lines))
            text = "\n".join(lines[:head] + notes + lines[head:])
        clipboard = QApplication.clipboard()
        if (
            clipboard is None
        ):  # no clipboard (no QApplication/display): say so instead of reporting a copy that did not happen
            log.error("napari: no clipboard to copy the %s to", kind)
            self.status.setText(_failure_line("there is no clipboard to copy to"))
            return None
        clipboard.setText(text)
        self.status.setText(
            "✔ copied the %s to the clipboard"
            % ("Python snippet" if kind == "python" else "terminal command")
        )
        return text

    def _command_notes(self) -> list[str]:
        """One comment line for everything the command cannot say: a selection (not copied) and a chosen channel (the command
        sends the whole file). Same sentences and order as the other hosts, per parameter in the tool's order.
        """
        notes = []
        for param in cast(Tool, self.tool)["inputs"]:
            name = param["name"]
            toggle = self.region_toggles.get(name)
            if toggle is not None and toggle.value:
                notes.append(
                    "# %s: the selection cannot be copied; save it as a label image and put its path here"
                    % name
                )
                continue
            picked = self._picked_channel(name)
            if picked is not None:
                notes.append(
                    "# %s: napari sent only channel %d; the command sends the whole file" % (name, picked + 1)
                )
        return notes

    def _picked_channel(self, name: str) -> int | None:
        """The index of the channel the person chose for image parameter `name`, or None when there is nothing to choose."""
        box = self.channel_boxes.get(name)
        if box is None or box.native.isHidden() or box.value in ("", None):
            return None
        return list(box.choices).index(box.value)

    def _set_running(self, running: bool) -> None:
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

    def cancel(self, *_: Any) -> None:
        if not self.task or self.task.done.is_set():
            return
        self.status.setText("cancelling…")
        task, worker = self.task, self.worker
        task.cancel()
        # Escalate to killing the worker if the tool ignores Cancel. Bound to THIS task, so a stale timer
        # from an earlier run can never kill a later run's worker.
        QTimer.singleShot(int(CANCEL_GRACE_S * 1000), lambda: None if task.done.is_set() else worker.kill())

    def _on_progress(self, message: str | None, fraction: float | None) -> None:
        if fraction is None:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(100 * fraction))
        self.status.setText(message or "")

    def _on_done(self, task: Any) -> None:
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
            self.status.setText(_unfinished_text(task))
            return
        summaries = self._show_results(task)
        if summaries is None:
            return
        self._report_success(summaries)
        self._clear_after_run()
        self._schedule_choices(CHOICES_AFTER_RUN_MS)

    def _show_results(self, task: Any) -> list[str] | None:
        """Show every result of a finished task: the summaries, or None after reporting that they could not be shown."""
        try:
            return [self.presenter.show(result) for result in task.outputs["results"]]
        except Exception as error:  # noqa: BLE001 - some results may already be shown: do not claim success
            log.error("napari: could not show the results of %s", self._run_tool["id"], exc_info=True)
            self._show_failure(
                "the tool finished but its results could not be shown completely: %s" % error, error
            )
            return None
        finally:
            shutil.rmtree(
                self._job_dir, ignore_errors=True
            )  # inputs and outputs share the job dir; results are in the viewer

    def _report_success(self, summaries: list[str]) -> None:
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

    def _clear_after_run(self) -> None:
        """ClearAfterRun parameters go back to their default (or unset) once a run has succeeded."""
        tool = self._run_tool
        if self.gui is None or tool is not self.tool:
            return
        for p in cast(Tool, tool)["inputs"]:
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

    def _record_run(self, task: Any) -> None:
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
            lines += ["", "worker output (tail):", stderr[-STDERR_TAIL_CHARS:]]
        lines += ["", "log file: %s" % log.log_path()]  # not its tail: that would show earlier runs
        self.last_details = "\n".join(lines)
        self.details_button.setEnabled(True)

    def _show_details(self, *_: Any) -> None:
        from qtpy.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle("LabConstrictor - last run")
        box.setText("Run details (copy this when asking for help)")
        box.setDetailedText(self.last_details)
        box.exec_()

    def closeEvent(self, event: Any) -> None:  # Qt API name
        self._reaper.stop()
        if (
            self.task is not None and not self.task.done.is_set()
        ):  # closed during a run: stop it, do not leave worker or temp files
            try:
                self.worker.kill()
            except (
                OSError,
                subprocess.SubprocessError,
            ):  # best effort while closing: already gone / cannot be signalled
                log.error("napari: could not stop the worker while closing the widget", exc_info=True)
            shutil.rmtree(getattr(self, "_job_dir", ""), ignore_errors=True)
        self._workers.close_all()
        super().closeEvent(event)

    @property
    def tables(self) -> dict[str, list[list[str]]]:
        return self.presenter.tables if self.presenter else {}
