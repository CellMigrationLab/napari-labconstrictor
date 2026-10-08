"""Calibration units: napari layers (scale + units) -> micrometres per pixel."""

import unittest

import _paths  # noqa: F401  (must come first)
import numpy as np
from napari.layers import Image

from napari_labconstrictor._units import microns_per_pixel


def layer(scale, units=None):
    return Image(np.zeros((4, 4)), scale=(scale, scale), **({"units": units} if units else {}))


class Units(unittest.TestCase):
    def test_micrometres(self):
        self.assertEqual(microns_per_pixel(layer(0.65, "micrometer")), (0.65, False))

    def test_nanometres_are_converted(self):
        value, assumed = microns_per_pixel(layer(650.0, "nanometer"))
        self.assertAlmostEqual(value, 0.65)
        self.assertFalse(assumed)

    def test_millimetres_are_converted(self):
        value, _ = microns_per_pixel(layer(0.001, "millimeter"))
        self.assertAlmostEqual(value, 1.0)

    def test_scale_without_unit_is_assumed_micrometres_and_flagged(self):
        self.assertEqual(microns_per_pixel(layer(0.65)), (0.65, True))

    def test_uncalibrated_layer_has_no_calibration(self):
        self.assertEqual(microns_per_pixel(layer(1.0)), (None, False))


class Fallbacks(unittest.TestCase):
    def test_a_unit_that_is_not_a_length_is_logged_once_and_assumed(self):
        from unittest import mock

        from napari_labconstrictor import _units

        _units._unparsable_units.clear()
        with mock.patch.object(_units.log, "warning") as warn:
            self.assertEqual(microns_per_pixel(layer(0.65, "pixel")), (0.65, True))
            self.assertEqual(microns_per_pixel(layer(0.65, "pixel")), (0.65, True))
        self.assertEqual(warn.call_count, 1)

    def test_an_unreadable_tiff_is_reported_not_swallowed(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from napari_labconstrictor import _units

        path = Path(tempfile.mkdtemp()) / "bad.tif"
        path.write_bytes(b"not a tiff")
        with mock.patch.object(_units.log, "warning") as warn:
            yx, problem = _units.microns_yx_from_tiff_checked(path)
        self.assertIsNone(yx)
        self.assertIn("bad.tif", problem)
        warn.assert_called_once()
        self.assertIsNone(_units.microns_yx_from_tiff(path))
        self.assertIsNone(_units.microns_yx_from_tiff_checked(Path(tempfile.mkdtemp()) / "gone.tif")[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
