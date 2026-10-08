# Host features

## Fallbacks (intended)

Places where the widget deliberately carries on with a simpler behaviour instead of failing. Each one is logged
(in the LabConstrictor log file) when it happens, and the person is told where it matters.

| Where | What happens | What the person sees |
| --- | --- | --- |
| `_units._layer_microns` | A layer unit pint cannot turn into micrometres (napari's default `pixel`, an unknown or non-length unit): the scale is used as micrometres. Logged once per unit. | Status line: "calibration assumed to be in µm (layer has no unit)"; the pixel-size field can be edited. |
| `_units.microns_yx_from_tiff_checked` | A TIFF whose header cannot be read: no calibration is taken from it. Logged. | Status line: "could not read the pixel size from <file>: type it"; the field is left to be typed. |
| `_widget._channel_names` | An image file whose channels cannot be read: no channel list. Logged. | Status line: "could not read the channels of <file>"; the run reports the unreadable file. |
| `_widget._ask_choices` (ChoicesFrom) | The choices tool fails, answers without a list, or cannot run: the parameter stays a text field. Logged. | Status line: "could not get the choices for <parameter>: type the value". |
| `_results.ResultPresenter._table` | The previous table dock of a replaced output was already closed by the person: a new one is added. Logged. | Nothing (the new table appears). |
