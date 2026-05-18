"""
Fastener identification based on geometric heuristics.

Identifies likely fasteners (bolts, nuts, washers) by analysing:
- Volume relative to assembly total (small parts)
- Contact count (fasteners typically contact multiple parts)
- Optional: shape aspect ratio for bolt-like cylinders
"""

from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
import numpy as np


def identify_fasteners(parts, contacts, volume_ratio_threshold=0.05):
    """
    Identify fasteners among parts.

    Heuristics:
      1. Part volume < volume_ratio_threshold * total assembly volume
      2. Part contacts at least 2 other parts (or 3 if min_contacts specified)

    Args:
        parts: list of dicts with 'name' and 'shape' (TopoDS_Shape).
        contacts: list of contact dicts from detect_contacts().
        volume_ratio_threshold: max volume fraction to be considered a fastener.

    Returns:
        list[str]: Names of parts identified as fasteners.
    """
    # Compute per-part volumes and total
    volumes = {}
    total_volume = 0.0

    for part in parts:
        props = GProp_GProps()
        brepgprop.VolumeProperties(part["shape"], props)
        vol = props.Mass()
        volumes[part["name"]] = vol
        total_volume += vol

    # Count contacts per part
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
        if total_volume > 0 and vol < threshold and cnt >= 2:
            fasteners.append(name)

    return fasteners


def identify_fasteners_detailed(parts, contacts):
    """
    Detailed analysis returning scores for each part.
    Useful for debugging and UI display.

    Returns:
        list[dict]: Each dict has name, volume, contactCount, isFastener, score.
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

    result = []
    fasteners = set(identify_fasteners(parts, contacts))
    for part in parts:
        name = part["name"]
        vol = volumes.get(name, 0.0)
        cnt = contact_count.get(name, 0)
        result.append({
            "name": name,
            "volume_mm3": vol,
            "contactCount": cnt,
            "isFastener": name in fasteners,
        })
    return result
