"""Form values -> files for the worker: layers and selections written as TIFF, channel extraction. Pure helpers (no widget state)."""

import numpy as np

MAX_EXPORT_BYTES = 4 * 1024**3  # refuse to write a layer larger than this to a temporary TIFF


def is_set(path):
    return path is not None and str(path) not in ("", ".")


def _selected_shapes_layer(viewer, label):
    from napari.layers import Shapes

    layer = next((item for item in viewer.layers.selection if isinstance(item, Shapes)), None)
    if layer is None:
        raise ValueError(
            "'%s': select a Shapes layer in the layer list, or untick 'use the selection'" % label
        )
    if len(layer.data) == 0:
        raise ValueError("'%s': the selected Shapes layer '%s' has no shapes" % (label, layer.name))
    return layer


def _region_image_shape(label, source, image):
    """(yx shape, yx scale or None) of the image a region belongs to: the chosen file, else the chosen layer."""
    if source is not None and is_set(source.value):
        import tifffile

        with tifffile.TiffFile(str(source.value)) as tif:
            return tif.series[0].shape[-2:], None
    if image is not None:
        shape = (image.data[0] if getattr(image, "multiscale", False) else image.data).shape[-2:]
        return shape, tuple(image.scale[-2:])
    raise ValueError("'%s': choose the image it belongs to first" % label)


def selection_mask(viewer, param, source, image, job_dir):
    """The shapes of the selected Shapes layer as a label image the size of the image named by `param["region_of"]`
    (labels 1..N), written to `job_dir`. `source` is that image's "or file" widget (or None), `image` its layer (or None).
    Anything that makes this impossible is said to the person, never guessed around."""
    from tifffile import imwrite

    label = param["label"]
    layer = _selected_shapes_layer(viewer, label)
    shape, scale = _region_image_shape(label, source, image)
    if scale is not None and not np.allclose(layer.scale[-2:], scale):
        raise ValueError(
            "'%s': the Shapes layer '%s' has the scale %s but the image has %s; give them the same scale"
            % (label, layer.name, tuple(layer.scale[-2:]), scale)
        )
    mask = np.asarray(layer.to_labels(labels_shape=tuple(shape)), dtype=np.int32)
    if not mask.any():
        raise ValueError("'%s': the shapes of '%s' lie outside the image" % (label, layer.name))
    path = job_dir / (param["name"] + ".tif")
    imwrite(path, mask)
    return str(path)


def channel_file(path, index, label, target):
    """One channel of a multi-channel TIFF, written as its own TIFF."""
    import tifffile

    with tifffile.TiffFile(str(path)) as tif:
        series = tif.series[0]
        data, axes = series.asarray(), series.axes
    if "C" not in axes:
        return path
    if index >= data.shape[axes.index("C")]:
        raise ValueError(
            "'%s': channel %d was asked for, but the file has %d"
            % (label, index + 1, data.shape[axes.index("C")])
        )
    tifffile.imwrite(target, np.take(data, index, axis=axes.index("C")))
    return target


def export_layer(layer, picked, path):
    """Write an image/labels layer to `path` as TIFF (full resolution; one colour of an RGB layer when `picked` is an index)."""
    from tifffile import imwrite

    data = layer.data[0] if getattr(layer, "multiscale", False) else layer.data  # multiscale: full resolution
    size = int(np.prod(data.shape)) * np.dtype(data.dtype).itemsize
    if size > MAX_EXPORT_BYTES:
        raise ValueError(
            "layer '%s' is %.1f GB; exporting more than %.0f GB is not supported"
            % (layer.name, size / 1024**3, MAX_EXPORT_BYTES / 1024**3)
        )
    data = np.asarray(data)
    if picked is not None and getattr(layer, "rgb", False):  # PickChannel on an RGB layer: one colour
        data = data[..., picked]
    imwrite(path, data)
