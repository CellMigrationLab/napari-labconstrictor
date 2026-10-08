"""Schema -> `inspect.Signature` that magicgui turns into widgets. This is the whole schema->GUI translation."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Annotated, Any

Tool = dict[str, Any]  # one tool of a registry schema, as the JSON file holds it
Param = dict[str, Any]  # one input of a tool

_INT_RANGE = (-(2**31), 2**31 - 1)  # what a Qt spin box can hold: used only when the schema gives no bound
_FLOAT_RANGE = (-1e15, 1e15)  # likewise for a float spin box
_FINE_STEP = 1e-4  # Qt rounds to the step: small enough for calibrations such as 0.325 um/px
_COARSE_STEP = 0.01  # step of a float that is neither a unit-bearing value nor below 1
_PATH_TYPES = ("table", "file", "folder")
_NULLABLE_SCALARS = ("integer", "float", "string", "choice")
_LAYER_TYPES = ("image", "labels")


def presentation_order(tool: Tool) -> Tool:
    """The tool with its inputs in the order the form shows them: parameters of one `group` together (where the group first
    appears), `advanced` ones after all the others. Without group/advanced hints the tool is returned unchanged.
    """
    inputs = tool["inputs"]
    if not any(p.get("group") or p.get("advanced") for p in inputs):
        return tool
    first: dict[tuple[bool, str], int] = {}

    def rank(index: int, p: Param) -> int:
        group = p.get("group")
        if not group:
            return index
        return first.setdefault((bool(p.get("advanced")), group), index)

    order = sorted(enumerate(inputs), key=lambda ip: (bool(ip[1].get("advanced")), rank(*ip), ip[0]))
    return {**tool, "inputs": [p for _, p in order]}


def rule_satisfied(rule: dict[str, Any], value: Any) -> bool:
    """enabled_when: `equals` given = the value is one of them; otherwise the controlling parameter is set / true."""
    if "equals" in rule:
        return value in rule["equals"]
    return value is not None and value is not False and value != ""


def signature_from_schema(tool: Tool, layer_classes: dict[str, type]) -> inspect.Signature:
    """Build the signature for one tool. `layer_classes` maps 'image'/'labels' to napari layer classes."""
    parameters = [_parameter(p, layer_classes) for p in presentation_order(tool)["inputs"]]
    return inspect.Signature(parameters)


def _parameter(param: Param, layer_classes: dict[str, type]) -> inspect.Parameter:
    kind = param["type"]
    annotation, options = _annotation(param, layer_classes)
    if param.get("description"):
        options["tooltip"] = param["description"]
    options["label"] = param["label"] + (" (%s)" % param["unit"] if param.get("unit") else "")
    if not param["required"] and kind in _LAYER_TYPES + _PATH_TYPES:
        annotation = annotation | None  # nullable widget
    elif param.get("nullable") and kind in _NULLABLE_SCALARS:
        annotation = (
            annotation | None
        )  # "unset" is a value: the widget gets a None state, and the request omits it
        options["nullable"] = True
    default = param.get("default", inspect.Parameter.empty if param["required"] else None)
    if kind == "boolean" and default is None:
        default = False  # a check box cannot hold None; the form's "set" box decides whether the value is sent at all
    return inspect.Parameter(
        param["name"],
        inspect.Parameter.KEYWORD_ONLY,
        default=default,
        annotation=Annotated[annotation, options],
    )


def _annotation(param: Param, layer_classes: dict[str, type]) -> tuple[Any, dict[str, Any]]:
    kind = param["type"]
    options: dict[str, Any] = {}
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
        if param.get("widget") == "radio" and not param.get("nullable"):
            options["widget_type"] = "RadioButtons"
        return str, options
    if kind not in ("integer", "float"):
        raise ValueError("parameter %r has the unknown type %r" % (param["name"], kind))
    is_int = kind == "integer"
    low, high = _INT_RANGE if is_int else _FLOAT_RANGE
    options["min"] = param.get("minimum", low)
    options["max"] = param.get("maximum", high)
    if not is_int:
        # Qt rounds to the step, which must never corrupt calibrations such as 0.325 um/px.
        options["step"] = (
            _FINE_STEP if param.get("unit") or abs(param.get("default", 1)) < 1 else _COARSE_STEP
        )
    if (
        param.get("widget") == "slider"
        and not param.get("nullable")
        and "minimum" in param
        and "maximum" in param
    ):
        options["widget_type"] = (
            "Slider" if is_int else "FloatSlider"
        )  # the readout box beside it takes a typed value
    return (int if is_int else float), options
