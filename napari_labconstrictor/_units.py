"""Calibration handling: napari layers carry scale + units; the tools want micrometres per pixel."""


def microns_per_pixel(layer):
    """-> (value, assumed). `assumed` is True when the layer has a scale but no usable length unit (napari's default
    'pixel'), in which case micrometres are assumed - the caller should say so. -> (None, False) when uncalibrated.
    """
    scale = float(layer.scale[-1])
    units = getattr(layer, "units", None)
    if units:
        try:
            return scale * float((1 * units[-1]).to("micrometer").magnitude), False
        except Exception:  # noqa: BLE001 - dimensionless/pixel/unknown unit: pint raises various errors
            pass
    return (scale, True) if scale != 1.0 else (None, False)


_MICRONS_PER_UNIT = {"um": 1.0, "micron": 1.0, "microns": 1.0, "micrometer": 1.0, "micrometre": 1.0, "nm": 1e-3,
                     "nanometer": 1e-3, "mm": 1e3, "millimeter": 1e3, "cm": 1e4, "m": 1e6}  # fmt: skip
_RESOLUTION_UNIT_MICRONS = {2: 25400.0, 3: 10000.0}  # TIFF ResolutionUnit: inch, centimetre


def microns_from_tiff(path):
    """Pixel size (um) stored in a TIFF (ImageJ or plain TIFF resolution tags), or None when absent / unit unknown."""
    import tifffile

    try:
        with tifffile.TiffFile(str(path)) as tif:
            tags = tif.pages[0].tags
            if "XResolution" not in tags:
                return None
            numerator, denominator = tags["XResolution"].value
            if not numerator:
                return None
            pixel = denominator / numerator
            unit = (
                ((tif.imagej_metadata or {}).get("unit") or "")
                .lower()
                .replace("\u00b5", "u")
                .replace("\u03bc", "u")
            )
            if unit in _MICRONS_PER_UNIT:
                return pixel * _MICRONS_PER_UNIT[unit]
            code = tags["ResolutionUnit"].value if "ResolutionUnit" in tags else 2
            factor = _RESOLUTION_UNIT_MICRONS.get(int(code))
            return pixel * factor if factor and not unit else None
    except Exception:  # noqa: BLE001 - not a TIFF / unreadable: the user simply types the value
        return None
