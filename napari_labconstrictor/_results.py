"""Show typed tool results in napari. Switches on the result *type* only; knows nothing about any app."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from labconstrictor_tools import log
from qtpy.QtWidgets import QTableWidget, QTableWidgetItem

if TYPE_CHECKING:  # napari and Qt objects are annotated by name only (no usable stubs)
    from napari import Viewer
    from qtpy.QtWidgets import QWidget

Result = dict[str, Any]  # one typed result of a tool: {"type": ..., "name": ..., ...}

COULD_NOT_DISPLAY = "(could not display "
MAX_DISPLAY_BYTES = (
    4 * 1024**3
)  # a result image larger than this (uncompressed) is not loaded into the viewer
MAX_TABLE_ROWS = 100_000  # rows shown in a table dock; the file itself always has all of them


MAX_SHAPES = 50_000  # outlines shown; the rest are counted in a message
POINT_SIZE = 8  # display size of a point result (napari points are drawn this many pixels wide)
OUTLINE_EDGE_WIDTH = 1  # line width of a shapes result
AFFINE_OPACITY = 0.8  # an alignment overlay stays see-through, so the image under it can be compared


class FileInput:
    """Stands in for a layer when an image parameter was given as a file (needed to place affine results)."""

    def __init__(self, path: Path) -> None:
        from ._units import microns_yx_from_tiff

        self.path, self.name = path, getattr(path, "name", str(path))
        self.scale = microns_yx_from_tiff(path) or (1.0, 1.0)  # (y, x)

    @property
    def data(self) -> np.ndarray:
        from tifffile import imread

        return imread(self.path)


class ResultPresenter:
    """`show(result)` adds layers/docks to the viewer and returns a short text for the status line."""

    def __init__(
        self,
        viewer: Viewer,
        app_name: str,
        inputs: dict[Any, Any],  # looked up with .get(None) when a result names no input
        replace: Any = (),
        docks: dict[tuple[str, str], QWidget] | None = None,
    ) -> None:
        self.viewer, self.app, self.inputs = viewer, app_name, inputs  # inputs: the layers chosen in the form
        self.replace = set(replace)  # output names whose previous result this run replaces (Replace())
        self.docks = docks if docks is not None else {}  # (app, table name) -> dock widget kept between runs
        self.tables: dict[str, list[list[str]]] = {}
        self.messages: list[str] = []  # texts of message results, shown by the widget below the status line
        self._handlers = {
            "image": self._image,
            "labels": self._image,
            "affine": self._affine,
            "table": self._table,
            "values": self._values,
            "message": self._message,
            "points": self._points,
            "shapes": self._shapes,
            "file": self._file,
        }

    def show(self, result: Any) -> str:
        kind = result.get("type", "<no type>") if isinstance(result, dict) else "<not an object>"
        try:
            return self._handlers[kind](result) or ""
        except Exception as error:  # noqa: BLE001 - a display problem must not hide the other results
            log.error("napari: could not display a %s result", kind, exc_info=True)
            return "%s%s: %s)" % (
                COULD_NOT_DISPLAY,
                kind,
                repr(error) if isinstance(error, KeyError) else error,
            )

    def _layer_name(self, result: Result) -> str:
        return "%s:%s" % (self.app, result["name"])

    def _image(self, result: Result) -> None:
        import tifffile

        with tifffile.TiffFile(
            result["path"]
        ) as tif:  # look before loading: a huge result must not freeze the viewer
            series = tif.series[0]
            size = int(np.prod(series.shape)) * np.dtype(series.dtype).itemsize
        if size > MAX_DISPLAY_BYTES:
            raise ValueError(
                "the image is %.1f GB; showing more than %.0f GB is not supported (it is in %s)"
                % (size / 1024**3, MAX_DISPLAY_BYTES / 1024**3, result["path"])
            )
        data = tifffile.imread(result["path"])
        add = self.viewer.add_labels if result["type"] == "labels" else self.viewer.add_image
        name = self._layer_name(result)
        if result["name"] in self.replace and name in self.viewer.layers:
            old = self.viewer.layers[name]
            kind = type(old).__name__
            if kind == ("Labels" if result["type"] == "labels" else "Image") and old.data.ndim == data.ndim:
                old.data = data  # same layer: the view, contrast and position the user chose stay
                return
            self.viewer.layers.remove(old)
        add(data, name=name)

    def _affine(self, result: Result) -> None:
        source = self.inputs[result["apply_to"]]
        target = self.inputs.get(result.get("relative_to"))
        matrix = np.array(result["matrix_yx"])
        # The matrix maps SOURCE pixels -> TARGET pixels. napari applies layer.scale first and layer.affine after,
        # so keep the source in pixel units (scale=1) and left-multiply by the target's calibration.
        if target is not None:
            matrix = np.diag([target.scale[-2], target.scale[-1], 1.0]) @ matrix
        name = self._layer_name(result)
        if result["name"] in self.replace and name in self.viewer.layers:
            self.viewer.layers.remove(
                self.viewer.layers[name]
            )  # Replace(): the new alignment takes the place of the previous overlay
        self.viewer.add_image(
            source.data,
            name=name,
            affine=matrix,
            scale=(1, 1),
            colormap="magenta",
            blending="additive",
            opacity=AFFINE_OPACITY,
        )

    def _table(self, result: Result) -> str:
        import itertools

        with open(result["path"], encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            rows = list(itertools.islice(reader, MAX_TABLE_ROWS + 1))  # header + the rows that are shown
            hidden = sum(1 for _ in reader)  # counted, not kept
        self.tables[result["name"]] = rows
        widget = QTableWidget(len(rows) - 1, len(rows[0]))
        widget.setHorizontalHeaderLabels(rows[0])
        for i, row in enumerate(rows[1:]):
            for j, cell in enumerate(row):
                widget.setItem(i, j, QTableWidgetItem(cell))
        key = (self.app, result["name"])
        if result["name"] in self.replace and key in self.docks:
            try:
                self.viewer.window.remove_dock_widget(self.docks.pop(key))
            except (
                LookupError,
                RuntimeError,
            ) as error:  # the user closed it already (not found / Qt object deleted)
                log.warning(
                    "napari: the old '%s' table was already gone (%s: %s)",
                    result["name"],
                    type(error).__name__,
                    error,
                )
                self.docks.pop(key, None)
        self.viewer.window.add_dock_widget(widget, name=result["name"], area="bottom")
        self.docks[key] = widget
        shown = (
            "%d rows" % (len(rows) - 1)
            if not hidden
            else "%d rows, first %d shown" % (len(rows) - 1 + hidden, len(rows) - 1)
        )
        return "table '%s' (%s)" % (result["name"], shown)

    def _message(self, result: Result) -> None:
        self.messages.append(result["text"])

    def _points(self, result: Result) -> str:
        import pandas as pd

        frame = pd.read_csv(result["path"])
        data = frame[["y", "x"]].to_numpy(dtype=float)
        properties = {c: frame[c].to_numpy() for c in frame.columns[2:]}
        source = self.inputs.get(result.get("apply_to")) or next(iter(self.inputs.values()), None)
        scale = tuple(source.scale[-2:]) if source is not None and hasattr(source, "scale") else (1.0, 1.0)
        name = self._layer_name(result)
        if result["name"] in self.replace and name in self.viewer.layers:
            self.viewer.layers.remove(
                self.viewer.layers[name]
            )  # Replace(): the new points take the place of the previous ones
        self.viewer.add_points(
            data,
            name=name,
            properties=properties or None,
            scale=scale,
            size=POINT_SIZE,
            face_color="yellow",
            border_color="black",
        )
        return "points '%s' (%d)" % (result["name"], len(frame))

    def _shapes(self, result: Result) -> str:
        """GeoJSON outlines -> one polygon per part (Napari polygons have no holes: the outer boundary is drawn, and a note says so)."""
        import json

        collection = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        polygons: list[np.ndarray] = []
        rows: list[dict[str, Any]] = []
        with_holes = 0
        for feature in collection.get("features", []):
            geometry = feature["geometry"]
            parts = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
            for part in parts:
                ring = np.asarray(part[0], dtype=float)[:-1]  # closed ring: drop the repeated last vertex
                polygons.append(ring[:, ::-1])  # GeoJSON [x, y] -> Napari (y, x)
                rows.append(feature.get("properties") or {})
                with_holes += len(part) > 1
        total = len(polygons)
        if total > MAX_SHAPES:
            polygons, rows = polygons[:MAX_SHAPES], rows[:MAX_SHAPES]
        keys = sorted({k for row in rows for k in row})
        properties = {k: np.array([row.get(k, "") for row in rows]) for k in keys}
        source = self.inputs.get(result.get("apply_to")) or next(iter(self.inputs.values()), None)
        scale = tuple(source.scale[-2:]) if source is not None and hasattr(source, "scale") else (1.0, 1.0)
        name = self._layer_name(result)
        if result["name"] in self.replace and name in self.viewer.layers:
            self.viewer.layers.remove(
                self.viewer.layers[name]
            )  # Replace(): the new outlines take the place of the previous ones
        if polygons:
            self.viewer.add_shapes(
                polygons, shape_type="polygon", name=name, properties=properties or None, scale=scale,
                edge_color="yellow", face_color="transparent", edge_width=OUTLINE_EDGE_WIDTH,
            )  # fmt: skip
        else:
            self.viewer.add_shapes(name=name, scale=scale)
        if total > MAX_SHAPES:
            self.messages.append(
                "**%s**: showing the first %d of %d outlines." % (result["name"], MAX_SHAPES, total)
            )
        if with_holes:
            self.messages.append(
                "**%s**: %d outline(s) have holes; the layer draws only the outer boundary."
                % (result["name"], with_holes)
            )
        return "outlines '%s' (%d)" % (result["name"], len(polygons))

    @staticmethod
    def _values(result: Result) -> str:
        return ", ".join(
            "%s=%s" % (key, round(value, 4) if isinstance(value, float) else value)
            for key, value in result["values"].items()
        )

    @staticmethod
    def _file(result: Result) -> str:
        if not Path(result["path"]).is_file():
            raise FileNotFoundError("the tool reported the file %s but it does not exist" % result["path"])
        return "file: " + result["path"]
