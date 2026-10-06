# napari-labconstrictor

One generic Napari dock widget for **every installed LabConstrictor app**. Pick an app and a tool; the form is generated from the
tool's declared schema (see [LabConstrictor-Tools](https://github.com/CellMigrationLab/LabConstrictor-Tools)). The tool runs in the
app's own Python environment in a separate process, so Napari never imports the app's packages and apps with conflicting
dependencies coexist.

![widget](docs/screenshot.png)

## Install
In the Napari environment (Python >= 3.10):

    pip install git+https://github.com/CellMigrationLab/LabConstrictor-Tools      # the runtime (not on PyPI yet)
    pip install git+https://github.com/CellMigrationLab/napari-labconstrictor

Then **Plugins > LabConstrictor tools**. The widget lists the apps registered on this machine (the LabConstrictor installer
registers each app; for a manual registration see the Tools repository: `labconstrictor-tools register ...`).
`labconstrictor-tools doctor` shows what is registered and why an app might be skipped.

## What it does
* **Forms from schemas**: numbers with ranges and units, choices, checkboxes, file and folder pickers. A value that is optional and has no default (a seed, a time limit) has a **"set" checkbox**: unticked, it is greyed out and the tool receives `None`.
* **Longer forms made readable**: parameters of a `group` sit under a bold heading, `advanced` ones behind a **"Show advanced settings"** box (closed by default), and settings that do not apply to the current choice (`enabled_when`) are greyed out.
* **Image inputs from an open layer *or* a file**: each image parameter has a layer chooser and an "or file" row; a chosen file wins.
  TIFF/OME-TIFF always, other formats if the app has `imageio`. Tables are chosen as CSV files.
* **Calibration**: a pixel-size field linked to an image follows the layer (units converted to micrometres) or the TIFF header.
* **Typed results**: images/labels become layers, tables a table dock, alignments an affine-placed layer, values the status line.
* **"No match" is a notice, not an error**: a tool that fails with the code `no_match` / `no_result` is shown as a warning line, not a red cross.
* **Progress and Cancel**; a tool that ignores Cancel has its worker killed after 3 s. Optional worker reuse for fast repeat runs.
* **When something fails**: the status line says what, **Details...** shows the error, traceback, worker output and log tail, and
  everything is in `~/.labconstrictor/logs/labconstrictor.log` (`labconstrictor-tools support-bundle` zips it for a bug report).
  Every run also leaves `~/.labconstrictor/runs/<time>_<app>_<tool>/run.json`.

## Development and tests
    pip install -e ".[test]"                       # also install labconstrictor-tools (see above)
    cd tests
    python test_units.py
    xvfb-run -a python test_widget.py              # on Linux without a display; elsewhere run directly
    xvfb-run -a python test_file_sources.py
    xvfb-run -a python test_run_state.py
    xvfb-run -a python test_presentation.py        # groups, advanced toggle, enabled_when

The tests register the example app shipped with `labconstrictor-tools` (`labconstrictor_tools.examples.synthetic`) in a private
registry, so no real LabConstrictor app is needed. Real apps are tested in their own repositories.

Layout: `napari_labconstrictor/` = `_widget.py` (the dock widget), `_schema.py` (schema -> magicgui signature), `_results.py`,
`_units.py`, `_workers.py` (worker cache), `napari.yaml` (npe2 manifest).

Status: **testing phase**. Tested on Linux (Qt5, Napari 0.9) under Xvfb. Windows and macOS are untested: to help, follow the
[human test protocol](https://github.com/CellMigrationLab/LabConstrictor-Tools/blob/main/docs/HUMAN_TEST_PROTOCOL.md). License: MIT.
