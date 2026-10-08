"""Host parity H08 and H15 (NP-7, NP-8): the outlines cap counts features (the tool's objects), not polygon parts; a result
type this version does not know gets one clear sentence with its name, type and file."""

import json
import tempfile
from pathlib import Path

import _paths  # noqa: F401  (must come first)
from _harness import expect, finish, viewer

from napari_labconstrictor._results import COULD_NOT_DISPLAY, MAX_SHAPES, ResultPresenter

work = Path(tempfile.mkdtemp(prefix="lcres_"))


def square(i):
    x = i % 300
    return [[x, 0], [x + 0.5, 0], [x, 0.5], [x, 0]]


# NP-7: 60 features of two parts each: the cap (patched to 50) is on features, so 50 features = 100 polygons are drawn
features = [
    {
        "type": "Feature",
        "properties": {"label": i},
        "geometry": {"type": "MultiPolygon", "coordinates": [[square(i)], [square(i + 1)]]},
    }
    for i in range(60)
]
(work / "multi.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
import napari_labconstrictor._results as results

results.MAX_SHAPES = 50
presenter = ResultPresenter(viewer, "app", {}, replace=(), docks={})
summary = presenter.show({"type": "shapes", "name": "multi", "path": str(work / "multi.geojson"), "n": 60})
results.MAX_SHAPES = MAX_SHAPES
expect(
    "cap_counts_features_not_parts",
    len(viewer.layers["app:multi"].data) == 100,
    len(viewer.layers["app:multi"].data),
)
expect(
    "cap_sentence_counts_features",
    presenter.messages == ["**multi**: showing the first 50 of 60 outlines."],
    presenter.messages,
)
expect("summary_counts_features", summary == "outlines 'multi' (50)", summary)

# fewer features than the cap: no sentence, nothing left out even with several parts each
presenter = ResultPresenter(viewer, "app2", {}, replace=(), docks={})
presenter.show({"type": "shapes", "name": "multi", "path": str(work / "multi.geojson"), "n": 60})
expect(
    "below_the_cap_everything_is_shown",
    len(viewer.layers["app2:multi"].data) == 120 and not presenter.messages,
    (len(viewer.layers["app2:multi"].data), presenter.messages),
)

# NP-8: an unknown type
presenter = ResultPresenter(viewer, "app", {})
text = presenter.show({"type": "heatmap", "name": "h", "path": "/x/h.bin"})
expect(
    "unknown_type_sentence",
    text
    == COULD_NOT_DISPLAY
    + "heatmap: the tool returned a result of the type 'heatmap', which this version of napari LabConstrictor cannot show (name 'h', file /x/h.bin))",
    text,
)
text = presenter.show({"type": "heatmap", "name": "h"})
expect("unknown_type_without_a_file", text.endswith("cannot show (name 'h'))"), text)
expect(
    "a_result_without_a_type_is_still_an_error", presenter.show({"name": "x"}).startswith(COULD_NOT_DISPLAY)
)
finish()
