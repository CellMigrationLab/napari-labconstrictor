"""Calibration per axis, the schema->widget translation, the result presenter on bad input, and the worker cache bookkeeping."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import _paths  # noqa: F401  (must come first)
import numpy as np
import tifffile
from napari.layers import Image
from qtpy.QtWidgets import QApplication

from napari_labconstrictor import _results, _schema, _units, _workers

_qt = QApplication.instance() or QApplication([])


class Calibration(unittest.TestCase):
    def test_a_layer_with_different_y_and_x_scale_keeps_both(self):
        layer = Image(np.zeros((4, 4)), scale=(0.5, 0.25), units=("micrometer", "micrometer"))
        yx, assumed = _units.microns_per_pixel_yx(layer)
        self.assertEqual((yx, assumed), ((0.5, 0.25), False))
        self.assertEqual(_units.microns_per_pixel(layer), (0.25, False))  # a single-value tool gets X ...
        self.assertIn("Y 0.5", _units.anisotropy_note(yx))  # ... and the user is told
        self.assertIn("X 0.25", _units.anisotropy_note(yx))

    def test_square_pixels_give_no_warning(self):
        self.assertIsNone(_units.anisotropy_note((0.325, 0.325)))
        self.assertIsNone(_units.anisotropy_note(None))

    def test_a_tiff_with_different_y_and_x_resolution_keeps_both(self):
        path = Path(tempfile.mkdtemp()) / "a.tif"
        tifffile.imwrite(
            path, np.zeros((4, 4), np.uint8), imagej=True, resolution=(4.0, 2.0), metadata={"unit": "um"}
        )
        self.assertEqual(_units.microns_yx_from_tiff(path), (0.5, 0.25))
        self.assertEqual(_units.microns_from_tiff(path), 0.25)
        self.assertEqual(_results.FileInput(path).scale, (0.5, 0.25))  # not 0.25 twice

    def test_a_file_without_calibration_is_uncalibrated(self):
        path = Path(tempfile.mkdtemp()) / "b.tif"
        tifffile.imwrite(path, np.zeros((4, 4), np.uint8))
        self.assertIsNone(_units.microns_yx_from_tiff(path))
        self.assertEqual(_results.FileInput(path).scale, (1.0, 1.0))


class Schema(unittest.TestCase):
    def test_an_unknown_parameter_type_is_refused_not_turned_into_a_float(self):
        with self.assertRaises(ValueError) as caught:
            _schema._annotation({"name": "x", "type": "imagge"}, {})
        self.assertIn("imagge", str(caught.exception))

    def test_a_number_without_bounds_is_not_capped_at_a_billion(self):
        _, options = _schema._annotation({"name": "n", "type": "integer"}, {})
        self.assertGreaterEqual(options["max"], 2_000_000_000)
        self.assertEqual(_schema._annotation({"name": "n", "type": "integer", "maximum": 7}, {})[1]["max"], 7)
        _, options = _schema._annotation({"name": "f", "type": "float"}, {})
        self.assertGreaterEqual(options["max"], 1e12)


class Presenter(unittest.TestCase):
    def setUp(self):
        self.shown = []
        viewer = SimpleNamespace(
            add_image=lambda data, **kw: self.shown.append(("image", data.shape)),
            add_labels=lambda data, **kw: self.shown.append(("labels", data.shape)),
            window=SimpleNamespace(
                add_dock_widget=lambda widget, **kw: self.shown.append(("dock", widget.rowCount()))
            ),
        )
        self.presenter = _results.ResultPresenter(viewer, "app", {})
        self.dir = Path(tempfile.mkdtemp())

    def test_a_malformed_result_does_not_crash_the_error_handler(self):
        for bad in ({}, {"name": "x"}, None, [1], {"type": "nonsense"}):
            text = self.presenter.show(bad)
            self.assertTrue(text.startswith(_results.COULD_NOT_DISPLAY), (bad, text))

    def test_a_missing_output_file_is_not_presented_as_a_result(self):
        text = self.presenter.show({"type": "file", "name": "report", "path": str(self.dir / "gone.txt")})
        self.assertTrue(text.startswith(_results.COULD_NOT_DISPLAY))
        self.assertIn("does not exist", text)
        existing = self.dir / "here.txt"
        existing.write_text("x")
        self.assertEqual(
            self.presenter.show({"type": "file", "name": "r", "path": str(existing)}), "file: %s" % existing
        )

    def test_an_image_larger_than_the_limit_is_refused_before_loading(self):
        path = self.dir / "big.tif"
        tifffile.imwrite(path, np.zeros((64, 64), np.uint16))
        with mock.patch.object(_results, "MAX_DISPLAY_BYTES", 1000), mock.patch("tifffile.imread") as read:
            text = self.presenter.show({"type": "image", "name": "big", "path": str(path)})
            read.assert_not_called()
        self.assertTrue(text.startswith(_results.COULD_NOT_DISPLAY))
        self.assertIn("GB", text)
        self.assertEqual(self.presenter.show({"type": "image", "name": "ok", "path": str(path)}), "")
        self.assertEqual(self.shown, [("image", (64, 64))])

    def test_a_huge_table_shows_a_bounded_number_of_rows_and_says_so(self):
        path = self.dir / "t.csv"
        path.write_text("a,b\n" + "".join("%d,%d\n" % (i, i) for i in range(10)))
        with mock.patch.object(_results, "MAX_TABLE_ROWS", 3):
            text = self.presenter.show({"type": "table", "name": "t", "path": str(path)})
        self.assertEqual(text, "table 't' (10 rows, first 3 shown)")
        self.assertEqual(self.shown, [("dock", 3)])
        small = self.presenter.show({"type": "table", "name": "t2", "path": str(path)})
        self.assertEqual(small, "table 't2' (10 rows)")


class FakeWorker:
    def __init__(self, app=None):
        self.alive, self.closed = True, False

    def close(self, timeout=0):
        self.alive, self.closed = False, True


class WorkerCache(unittest.TestCase):
    def cache(self):
        with mock.patch.object(_workers.atexit, "register"):
            cache = _workers.WorkerCache()
        return cache

    def test_close_all_also_closes_a_worker_that_is_running_a_task(self):
        cache = self.cache()
        with mock.patch.object(_workers.client, "WorkerProcess", FakeWorker):
            running = cache.acquire("app")
        cache.close_all()
        self.assertTrue(running.closed)

    def test_two_workers_released_for_one_app_do_not_leak_the_first(self):
        cache = self.cache()
        with mock.patch.object(_workers.client, "WorkerProcess", FakeWorker):
            first = cache.acquire("app")
            second = cache.acquire("app")
        cache.release("app", first, keep=True)
        cache.release("app", second, keep=True)
        self.assertTrue(first.closed)  # displaced by the second, closed rather than forgotten
        self.assertFalse(second.closed)
        self.assertEqual(cache._active, set())
        cache.close_all()
        self.assertTrue(second.closed)

    def test_a_released_worker_is_no_longer_active_and_is_reused(self):
        cache = self.cache()
        with mock.patch.object(_workers.client, "WorkerProcess", FakeWorker):
            worker = cache.acquire("app")
            cache.release("app", worker, keep=True)
            self.assertIs(cache.acquire("app"), worker)


if __name__ == "__main__":
    unittest.main()
