# Documentation map

Layers in, layers out.

The [README](../README.md) is the entry point. Detailed material belongs in the sections below. This map records the intended structure; a named page may still need to be created or updated by the implementation maintainers.

| Section | Intended file | Scope |
|---|---|---|
| Overview and first run | `README.md` | Dock widget, Playground first run, expected layers |
| Inputs and forms | `docs/INPUTS.md` | Layer/file precedence, Shapes selections, channels, scale and dependent controls |
| Results | `docs/RESULTS.md` | Image, Labels, Points, Shapes, tables, affine, replacement and limits |
| Install and troubleshoot | `docs/INSTALL.md` | Napari/Python requirements, app registration, errors and logs |
| Testing | `docs/TESTING.md` | Widget tests, headless/Xvfb setup, platform coverage |

## Maintenance

Document what users see in Napari; link to the Toolkit for protocol details. Code owners should verify result presentation and first-run screenshot.

When changing a tool or host behavior, update the relevant section in the same PR. Prefer one tested example to several unverified ones. Mark unsupported behavior explicitly; do not turn planned features into present-tense claims.
