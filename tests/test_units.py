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


if __name__ == "__main__":
    unittest.main(verbosity=2)
