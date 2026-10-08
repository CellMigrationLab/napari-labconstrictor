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

## Intended differences from the other hosts

* **A stack or multi-channel image given to a 2D tool (`Axes("YX")`) is refused, never flattened.** The host does not pick a plane of Z or T for the person: the worker answers `[wrong_dimensions] 'L' must be a 2D image (YX) but got 3D with shape (...)`, the same sentence as the command line and the notebook, and the status line shows it. A channel chosen with PickChannel from a file that also has a Z axis is still a stack and is refused the same way. Why: choosing data silently is the unsafe side of this divergence (Fiji sends the plane on screen, QuPath plane 0); the durable fix is a `PickPlane` hint in Tools (TOOLS-5), after which every host sends a plane on purpose.
* **A selection (RegionOf) belongs to an open layer.** With an image given as a file, the run is refused (`'L': the selection belongs to an open image, but a file was chosen for 'L2': open the image, or untick the selection`), as in Fiji and QuPath. At most 65 535 shapes are supported, and the label image is 16 bit.
