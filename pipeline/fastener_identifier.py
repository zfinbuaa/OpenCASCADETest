"""
Fastener identification based on geometric heuristics.

Identifies likely fasteners (bolts, nuts, washers) by analysing:
- Volume relative to assembly total (very small parts only)
- Contact count (fasteners typically contact multiple parts)
- Shape aspect ratio (fasteners are typically elongated)
- Absolute volume cap relative to median part size
"""

from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
import numpy as np


def _compute_bbox_aspect(shape):
    """Compute the aspect ratio (longest/shortest axis) of a shape's bbox."""
    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    if bbox.IsVoid():
        return 1.0
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    extents = [xmax - xmin, ymax - ymin, zmax - zmin]
    min_ext = min(extents)
    if min_ext < 1e-10:
        return 1.0
    return max(extents) / min_ext


def identify_fasteners(parts, contacts, volume_ratio_threshold=0.005):
    """
    Identify fasteners among parts.

    Heuristics (all must be true):
      1. Part volume < volume_ratio_threshold * total assembly volume
      2. Part contacts at least 2 other parts
      3. Part volume < median_volume * 0.05 (absolute cap)
      4. If aspect ratio > 3.0, relax volume condition to ratio_threshold * 2

    Args:
        parts: list of dicts with 'name' and 'shape' (TopoDS_Shape).
        contacts: list of contact dicts from detect_contacts().
        volume_ratio_threshold: max volume fraction to be considered a fastener.

    Returns:
        list[str]: Names of parts identified as fasteners.
    """
    volumes = {}
    total_volume = 0.0

    for part in parts:
        props = GProp_GProps()
        brepgprop.VolumeProperties(part["shape"], props)
        vol = props.Mass()
        volumes[part["name"]] = vol
        total_volume += vol

    vol_list = sorted(volumes.values())
    median_vol = vol_list[len(vol_list) // 2] if vol_list else 1.0
    absolute_cap = median_vol * 0.05

    contact_count = {}
    for c in contacts:
        for name in (c["partA"], c["partB"]):
            contact_count[name] = contact_count.get(name, 0) + 1

    fasteners = []
    threshold = total_volume * volume_ratio_threshold

    for part in parts:
        name = part["name"]
        vol = volumes.get(name, 0.0)
        cnt = contact_count.get(name, 0)

        if total_volume <= 0 or cnt < 2:
            continue

        if vol >= absolute_cap:
            continue

        aspect = _compute_bbox_aspect(part["shape"])
        effective_threshold = threshold * 2 if aspect > 3.0 else threshold

        if vol < effective_threshold:
            fasteners.append(name)

    return fasteners


def identify_fasteners_detailed(parts, contacts):
    """
    Detailed analysis returning scores for each part.

    Returns:
        list[dict]: Each dict has name, volume, contactCount, isFastener, aspect.
    """
    volumes = {}
    total_volume = 0.0
    for part in parts:
        props = GProp_GProps()
        brepgprop.VolumeProperties(part["shape"], props)
        vol = props.Mass()
        volumes[part["name"]] = vol
        total_volume += vol

    contact_count = {}
    for c in contacts:
        for name in (c["partA"], c["partB"]):
            contact_count[name] = contact_count.get(name, 0) + 1

    fasteners = set(identify_fasteners(parts, contacts))
    result = []
    for part in parts:
        name = part["name"]
        vol = volumes.get(name, 0.0)
        cnt = contact_count.get(name, 0)
        aspect = _compute_bbox_aspect(part["shape"])
        result.append({
            "name": name,
            "volume_mm3": vol,
            "contactCount": cnt,
            "isFastener": name in fasteners,
            "aspectRatio": round(aspect, 2),
        })
    return result
