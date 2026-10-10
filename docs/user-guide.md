# Install the napari plugin

One dock widget shows every installed LabConstrictor app. You pick an app and a tool, and the form is generated from the tool's declaration.

> [!WARNING]
> **Under heavy construction.** This plugin is untested and unlikely to be stable. Expect bugs, missing features and changes without notice. Do not rely on it for work you cannot redo.
>
> Tested on Linux. Windows and macOS are untested. The plugin is installed from GitHub; it is not on PyPI yet.

## What you need

- [napari](https://napari.org), in a Python environment with **Python 3.10 or newer**.
- At least one LabConstrictor app installed on your computer (the [apps that work in napari](https://labconstrictor.cellmig.org/apps/?host=napari) are listed in the App Centre). Installing the app and installing this plugin are two separate steps: the app's installer registers it on your computer, which is how the plugin finds it.

## Install

Run these commands in the same environment you start napari from. If you start napari from a terminal after `conda activate <name>` (or after activating a virtual environment), activate that environment first. If you installed napari with its desktop installer, follow [napari](https://napari.org)'s instructions for adding packages to that installation. Then install the tools runtime and the plugin:

    pip install https://github.com/CellMigrationLab/LabConstrictor-Tools/archive/refs/heads/main.zip
    pip install https://github.com/CellMigrationLab/napari-labconstrictor/archive/refs/heads/main.zip

These are source archives, so you do not need `git`. Restart napari.

## Open it

Choose **Plugins > LabConstrictor tools**. The widget lists the apps registered on your computer. Pick an app, pick a tool, set the parameters, and press Run.

- Image inputs take an open layer or a file (a chosen file wins). TIFF and OME-TIFF always work; other formats work if the app has `imageio`.
- Results appear as layers, a table, or in the status line. A tool that finds nothing shows a notice, not an error.
- A progress bar and a Cancel button are there while a tool runs.

## If something does not work

- The status line says what failed; **Details...** shows the error, the worker's output and the end of the log.
- `labconstrictor-tools doctor` shows which apps are registered and why one might be skipped.
- `labconstrictor-tools support-bundle` zips the log for a bug report. The log is `~/.labconstrictor/logs/labconstrictor.log`.

Source and issues: [napari-labconstrictor](https://github.com/CellMigrationLab/napari-labconstrictor).
