"""Calibration handling: napari layers carry scale + units; the tools want micrometres per pixel."""


def _layer_microns(scale, unit):
    """(micrometres per pixel, assumed) for one axis. `assumed`: a scale with no usable length unit (napari's 'pixel')."""
    scale = float(scale)
    if unit:
        try:
            return scale * float((1 * unit).to("micrometer").magnitude), False
        except Exception:  # noqa: BLE001 - dimensionless/pixel/unknown unit: pint raises various errors
            pass
    return scale, True


def microns_per_pixel_yx(layer):
    """-> ((y, x) micrometres per pixel, assumed), or (None, False) when the layer is uncalibrated (scale 1 on both axes)."""
    scale = [float(v) for v in layer.scale]
    scale_y, scale_x = (scale[-2], scale[-1]) if len(scale) >= 2 else (scale[-1], scale[-1])
    if scale_y == 1.0 and scale_x == 1.0:
        return None, False
    units = getattr(layer, "units", None)
    unit_y, unit_x = (units[-2], units[-1]) if units and len(units) >= 2 else (units[-1] if units else None,) * 2
    (y, assumed_y), (x, assumed_x) = _layer_microns(scale_y, unit_y), _layer_microns(scale_x, unit_x)
    return (y, x), assumed_y or assumed_x


def microns_per_pixel(layer):
    """-> (value, assumed) for the X axis (what a tool with ONE pixel-size parameter receives; see `anisotropy_note`).
    `assumed` is True when the layer has a scale but no usable length unit (napari's default 'pixel'), in which case
    micrometres are assumed - the caller should say so. -> (None, False) when uncalibrated.
    """
    yx, assumed = microns_per_pixel_yx(layer)
    return (None, False) if yx is None else (yx[1], assumed)


def anisotropy_note(yx):
    """Text for the user when the pixel is not square (a single pixel-size value cannot describe it), else None."""
    if yx is None or abs(yx[0] - yx[1]) <= 1e-3 * max(abs(yx[0]), abs(yx[1])):
        return None
    return "⚠ pixel size differs: Y %.6g µm, X %.6g µm - the tool takes one value and gets X" % yx


_MICRONS_PER_UNIT = {"um": 1.0, "micron": 1.0, "microns": 1.0, "micrometer": 1.0, "micrometre": 1.0, "nm": 1e-3,
                     "nanometer": 1e-3, "mm": 1e3, "millimeter": 1e3, "cm": 1e4, "m": 1e6}  # fmt: skip
_RESOLUTION_UNIT_MICRONS = {2: 25400.0, 3: 10000.0}  # TIFF ResolutionUnit: inch, centimetre


def microns_from_tiff(path):
    """Pixel size (um) along X stored in a TIFF (ImageJ or plain TIFF resolution tags), or None when absent / unit unknown."""
    yx = microns_yx_from_tiff(path)
    return None if yx is None else yx[1]


def microns_yx_from_tiff(path):
    """(y, x) pixel size (um) stored in a TIFF, or None when absent / unit unknown. A missing YResolution means square."""
    import tifffile

    try:
        with tifffile.TiffFile(str(path)) as tif:
            tags = tif.pages[0].tags
            x = _tag_pixel(tags, "XResolution")
            if x is None:
                return None
            y = _tag_pixel(tags, "YResolution")
            y = x if y is None else y
            unit = (
                ((tif.imagej_metadata or {}).get("unit") or "")
                .lower()
                .replace("\u00b5", "u")
                .replace("\u03bc", "u")
            )
            if unit in _MICRONS_PER_UNIT:
                factor = _MICRONS_PER_UNIT[unit]
            else:
                code = tags["ResolutionUnit"].value if "ResolutionUnit" in tags else 2
                factor = _RESOLUTION_UNIT_MICRONS.get(int(code))
                if not factor or unit:
                    return None
            return y * factor, x * factor
    except Exception:  # noqa: BLE001 - not a TIFF / unreadable: the user simply types the value
        return None


def _tag_pixel(tags, name):
    if name not in tags:
        return None
    numerator, denominator = tags[name].value
    return denominator / numerator if numerator else None
