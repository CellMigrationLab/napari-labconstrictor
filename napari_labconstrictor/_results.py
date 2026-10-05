"""Show typed tool results in napari. Switches on the result *type* only; knows nothing about any app."""

import csv

import numpy as np
from qtpy.QtWidgets import QTableWidget, QTableWidgetItem


class FileInput:
    """Stands in for a layer when an image parameter was given as a file (needed to place affine results)."""

    def __init__(self, path):
        from ._units import microns_from_tiff

        self.path, self.name = path, getattr(path, "name", str(path))
        self.scale = (microns_from_tiff(path) or 1.0,) * 2

    @property
    def data(self):
        from tifffile import imread

        return imread(self.path)


class ResultPresenter:
    """`show(result)` adds layers/docks to the viewer and returns a short text for the status line."""

    def __init__(self, viewer, app_name, inputs):
        self.viewer, self.app, self.inputs = viewer, app_name, inputs  # inputs: the layers chosen in the form
        self.tables = {}
        self._handlers = {
            "image": self._image,
            "labels": self._image,
            "affine": self._affine,
            "table": self._table,
            "values": self._values,
            "file": self._file,
        }

    def show(self, result):
        try:
            return self._handlers[result["type"]](result) or ""
        except Exception as error:  # noqa: BLE001 - a display problem must not hide the other results
            return "(could not display %s: %s)" % (result["type"], error)

    def _layer_name(self, result):
        return "%s:%s" % (self.app, result["name"])

    def _image(self, result):
        from tifffile import imread

        data = imread(result["path"])
        add = self.viewer.add_labels if result["type"] == "labels" else self.viewer.add_image
        add(data, name=self._layer_name(result))

    def _affine(self, result):
        source = self.inputs[result["apply_to"]]
        target = self.inputs.get(result.get("relative_to"))
        matrix = np.array(result["matrix_yx"])
        # The matrix maps SOURCE pixels -> TARGET pixels. napari applies layer.scale first and layer.affine after,
        # so keep the source in pixel units (scale=1) and left-multiply by the target's calibration.
        if target is not None:
            matrix = np.diag([target.scale[-2], target.scale[-1], 1.0]) @ matrix
        self.viewer.add_image(
            source.data,
            name=self._layer_name(result),
            affine=matrix,
            scale=(1, 1),
            colormap="magenta",
            blending="additive",
            opacity=0.8,
        )

    def _table(self, result):
        with open(result["path"], encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        self.tables[result["name"]] = rows
        widget = QTableWidget(len(rows) - 1, len(rows[0]))
        widget.setHorizontalHeaderLabels(rows[0])
        for i, row in enumerate(rows[1:]):
            for j, cell in enumerate(row):
                widget.setItem(i, j, QTableWidgetItem(cell))
        self.viewer.window.add_dock_widget(widget, name=result["name"], area="bottom")
        return "table '%s' (%d rows)" % (result["name"], len(rows) - 1)

    @staticmethod
    def _values(result):
        return ", ".join(
            "%s=%s" % (key, round(value, 4) if isinstance(value, float) else value)
            for key, value in result["values"].items()
        )

    @staticmethod
    def _file(result):
        return "file: " + result["path"]
