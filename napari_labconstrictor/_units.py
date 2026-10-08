"""Calibration handling: napari layers carry scale + units; the tools want micrometres per pixel."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from labconstrictor_tools import log
from pint.errors import PintError

# What reading a TIFF header can realistically raise: a file that is missing or unreadable (OSError), not a TIFF
# (tifffile.TiffFileError is a ValueError), a damaged or odd tag (ValueError/KeyError/IndexError/TypeError/struct.error).
TIFF_READ_ERRORS = (OSError, ValueError, KeyError, IndexError, TypeError, struct.error)

SQUARE_PIXEL_TOLERANCE = 1e-3  # Y and X pixel sizes closer than this (relative) count as square: no warning

_unparsable_units: set[str] = set()  # units already reported: the log says it once, not once per call


def _layer_microns(scale: float, unit: Any) -> tuple[float, bool]:
    """(micrometres per pixel, assumed) for one axis. `assumed`: a scale with no usable length unit (napari's 'pixel')."""
    scale = float(scale)
    if unit:
        try:
            return scale * float((1 * unit).to("micrometer").magnitude), False
        except (
            PintError,
            TypeError,
            AttributeError,
        ) as error:  # dimensionless ("pixel"), unknown or non-pint unit
            # Intended fallback: the scale is taken as micrometres and the caller tells the person ("assumed").
            if str(unit) not in _unparsable_units:
                _unparsable_units.add(str(unit))
                log.warning(
                    "napari: unit %r is not a length (%s: %s); assuming micrometres",
                    unit,
                    type(error).__name__,
                    error,
                )
    return scale, True


def microns_per_pixel_yx(layer: Any) -> tuple[tuple[float, float] | None, bool]:
    """-> ((y, x) micrometres per pixel, assumed), or (None, False) when the layer is uncalibrated (scale 1 on both axes)."""
    scale = [float(v) for v in layer.scale]
    scale_y, scale_x = (scale[-2], scale[-1]) if len(scale) >= 2 else (scale[-1], scale[-1])
    if scale_y == 1.0 and scale_x == 1.0:
        return None, False
    units = getattr(layer, "units", None)
    unit_y, unit_x = (
        (units[-2], units[-1]) if units and len(units) >= 2 else (units[-1] if units else None,) * 2
    )
    (y, assumed_y), (x, assumed_x) = _layer_microns(scale_y, unit_y), _layer_microns(scale_x, unit_x)
    return (y, x), assumed_y or assumed_x


def microns_per_pixel(layer: Any) -> tuple[float | None, bool]:
    """-> (value, assumed) for the X axis (what a tool with ONE pixel-size parameter receives; see `anisotropy_note`).
    `assumed` is True when the layer has a scale but no usable length unit (napari's default 'pixel'), in which case
    micrometres are assumed - the caller should say so. -> (None, False) when uncalibrated.
    """
    yx, assumed = microns_per_pixel_yx(layer)
    return (None, False) if yx is None else (yx[1], assumed)


def anisotropy_note(yx: tuple[float, float] | None) -> str | None:
    """Text for the user when the pixel is not square (a single pixel-size value cannot describe it), else None."""
    if yx is None or abs(yx[0] - yx[1]) <= SQUARE_PIXEL_TOLERANCE * max(abs(yx[0]), abs(yx[1])):
        return None
    return "⚠ pixel size differs: Y %.6g µm, X %.6g µm - the tool takes one value and gets X" % yx


_MICRONS_PER_UNIT = {"um": 1.0, "micron": 1.0, "microns": 1.0, "micrometer": 1.0, "micrometre": 1.0, "nm": 1e-3,
                     "nanometer": 1e-3, "mm": 1e3, "millimeter": 1e3, "cm": 1e4, "m": 1e6}  # fmt: skip
_RESOLUTION_UNIT_MICRONS = {2: 25400.0, 3: 10000.0}  # TIFF ResolutionUnit: inch, centimetre


def microns_from_tiff(path: str | Path) -> float | None:
    """Pixel size (um) along X stored in a TIFF (ImageJ or plain TIFF resolution tags), or None when absent / unit unknown."""
    yx = microns_yx_from_tiff(path)
    return None if yx is None else yx[1]


def microns_yx_from_tiff(path: str | Path) -> tuple[float, float] | None:
    """(y, x) pixel size (um) stored in a TIFF, or None when absent / unit unknown. A missing YResolution means square."""
    return microns_yx_from_tiff_checked(path)[0]


def microns_yx_from_tiff_checked(path: str | Path) -> tuple[tuple[float, float] | None, str | None]:
    """-> (yx, problem): `problem` is a sentence when the file could not be read (then yx is None), else None.
    A readable file without calibration gives (None, None). A failure is logged here, on every call."""
    import tifffile

    try:
        with tifffile.TiffFile(str(path)) as tif:
            tags = tif.pages[0].tags
            x = _tag_pixel(tags, "XResolution")
            if x is None:
                return None, None
            y = _tag_pixel(tags, "YResolution")
            y = x if y is None else y
            unit = (
                ((tif.imagej_metadata or {}).get("unit") or "")
                .lower()
                .replace("\u00b5", "u")
                .replace("\u03bc", "u")
            )
            if unit in _MICRONS_PER_UNIT:
                factor: float = _MICRONS_PER_UNIT[unit]
            else:
                code = tags["ResolutionUnit"].value if "ResolutionUnit" in tags else 2
                resolution_factor = _RESOLUTION_UNIT_MICRONS.get(int(code))
                if not resolution_factor or unit:
                    return None, None
                factor = resolution_factor
            return (y * factor, x * factor), None
    except (
        *TIFF_READ_ERRORS,
        tifffile.TiffFileError,
    ) as error:  # not a TIFF / unreadable: the person types the value
        log.warning("napari: cannot read the calibration of %s (%s: %s)", path, type(error).__name__, error)
        return None, "could not read the pixel size from %s (%s): type it" % (path, error)


def _tag_pixel(tags: Any, name: str) -> float | None:
    if name not in tags:
        return None
    numerator, denominator = tags[name].value
    return denominator / numerator if numerator else None
