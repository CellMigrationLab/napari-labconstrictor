"""Schema -> `inspect.Signature` that magicgui turns into widgets. This is the whole schema->GUI translation."""

import inspect
from pathlib import Path
from typing import Annotated

_INT_RANGE = (-(10**9), 10**9)
_FLOAT_RANGE = (-1e9, 1e9)
_PATH_TYPES = ("table", "file", "folder")
_NULLABLE_SCALARS = ("integer", "float", "string", "choice")
_LAYER_TYPES = ("image", "labels")


def signature_from_schema(tool, layer_classes):
    """Build the signature for one tool. `layer_classes` maps 'image'/'labels' to napari layer classes."""
    parameters = [_parameter(p, layer_classes) for p in tool["inputs"]]
    return inspect.Signature(parameters)


def _parameter(param, layer_classes):
    kind = param["type"]
    annotation, options = _annotation(param, layer_classes)
    if param.get("description"):
        options["tooltip"] = param["description"]
    options["label"] = param["label"] + (" (%s)" % param["unit"] if param.get("unit") else "")
    if not param["required"] and kind in _LAYER_TYPES + _PATH_TYPES:
        annotation = annotation | None  # nullable widget
    elif param.get("nullable") and kind in _NULLABLE_SCALARS:
        annotation = annotation | None  # "unset" is a value: the widget gets a None state, and the request omits it
        options["nullable"] = True
    default = param.get("default", inspect.Parameter.empty if param["required"] else None)
    return inspect.Parameter(
        param["name"],
        inspect.Parameter.KEYWORD_ONLY,
        default=default,
        annotation=Annotated[annotation, options],
    )


def _annotation(param, layer_classes):
    kind, options = param["type"], {}
    if kind in _LAYER_TYPES:
        return layer_classes[kind], options
    if kind in _PATH_TYPES:
        if kind == "folder":
            options["mode"] = "d"
        else:
            options["filter"] = "*.csv" if kind == "table" else "*"
        return Path, options
    if kind == "string":
        return str, options
    if kind == "boolean":
        return bool, options
    if kind == "choice":
        options["choices"] = param["choices"]
        return str, options
    is_int = kind == "integer"
    low, high = _INT_RANGE if is_int else _FLOAT_RANGE
    options["min"] = param.get("minimum", low)
    options["max"] = param.get("maximum", high)
    if not is_int:
        # Qt rounds to the step, which must never corrupt calibrations such as 0.325 um/px.
        options["step"] = 1e-4 if param.get("unit") or abs(param.get("default", 1)) < 1 else 0.01
    return (int if is_int else float), options
