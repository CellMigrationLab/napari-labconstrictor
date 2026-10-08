# Using LabConstrictor in Napari

The LabConstrictor widget lists tools registered by installed applications. Tools run in the application's Python environment and return results to Napari.

## Scientific applications

[Playground](https://github.com/CellMigrationLab/LabConstrictor-Playground) can test the widget. [Guess the Condition](https://github.com/CellMigrationLab/GuessTheCondition) and [VLab4Mic](https://github.com/CellMigrationLab/LabConstrictor-VLab4Mic) document Napari workflows. See the [Toolkit application list](https://github.com/CellMigrationLab/LabConstrictor-Tools#applications) for more applications and installation links.

## First run

Install [LabConstrictor Playground](https://github.com/CellMigrationLab/LabConstrictor-Playground) and open **Plugins > LabConstrictor tools**. Select **Feature tour**. Its optional image input can be left unset to generate a demonstration image.

Check that the result includes labels, points, outlines, a table and a message. This tests data exchange with the widget; it does not validate a scientific segmentation method.

## Image inputs

### Layer or file

Image inputs can come from a Napari layer or an image file. If both are supplied, the file takes precedence.

### Channels

`PickChannel()` can select an RGB channel or a channel from a multichannel file. Ordinary scalar image layers are already single-channel.

### Physical calibration

The widget can prefill pixel size from layer scale or image metadata when the tool declares a linked calibration parameter. Check units before quantitative analysis.

## Selections

A `RegionOf(...)` input can use a selected Shapes layer as a region mask. The tool must explicitly declare that input. A selected shape does not automatically crop arbitrary image inputs.

## Forms and long-running tools

Tool annotations supply ranges, units, choices and optional fields. Some inputs depend on other choices. Tools can report progress and respond to cancellation; unresponsive workers are terminated after a grace period.

## Results

| Return type | Napari |
|---|---|
| Image / labels | Image or Labels layer |
| Points / outlines | Points or Shapes layer |
| Table | Table display |
| Affine | Image/layer transform handling |
| Message / values / file | Result information |

Some outputs carry replacement metadata so repeated runs update an existing layer. Check which layer is selected before starting another run.

## Reproduce a run

Use the widget's copied command or Python snippet when available. Save inputs that currently exist only as in-memory layers before expecting a command-line run to reproduce them.

## Troubleshooting and tests

If the application or tool is missing, run `labconstrictor-tools list` and `labconstrictor-tools doctor`. Check the widget error details and shared log for worker failures.

The widget and conversion tests live under `tests/`. Maintainers should test image/file precedence, channel selection, Shapes masks, pixel size, each output type, cancellation and repeated runs when those code paths change.
