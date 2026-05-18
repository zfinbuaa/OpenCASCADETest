"""
Disassembly direction calculator — engineering-aware version.

Computes the preferred removal direction for each part based on:
1. Contact-area-weighted normal from the largest mating surface
2. Projection onto candidate directions (26 axes including diagonals)
3. Gravity bias (prefer upward removal)
4. Parent-child centroid direction (hierarchy awareness)
5. Bounding-box shortest axis fallback (mating face ⊥ shortest axis)

The old simple-average approach cancels symmetric normals and ignores
area weighting. This version picks the dominant mating face normal
and snaps it to a clean engineering direction.
"""

import numpy as np
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop


_CANDIDATE_DIRS = []

for _s in [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]:
    _CANDIDATE_DIRS.append(np.array(_s, dtype=np.float64))

for _sx in (-1, 1):
    for _sy in (-1, 1):
        for _sz in (-1, 1):
            _d = np.array([_sx, _sy, _sz], dtype=np.float64)
            _l = np.linalg.norm(_d)
            if _l > 0:
                _CANDIDATE_DIRS.append(_d / _l)

for _a in (-1, 1):
    for _b in (-1, 1):
        for _axis in range(3):
            _d = [0.0, 0.0, 0.0]
            _d[_axis] = 0.0
            _axes = [i for i in range(3) if i != _axis]
            _d[_axes[0]] = float(_a)
            _d[_axes[1]] = float(_b)
            _darr = np.array(_d, dtype=np.float64)
            _l = np.linalg.norm(_darr)
            if _l > 0:
                _CANDIDATE_DIRS.append(_darr / _l)

_seen = set()
_unique = []
for _d in _CANDIDATE_DIRS:
    _key = tuple(np.round(_d, 6))
    if _key not in _seen:
        _seen.add(_key)
        _unique.append(_d)
CANDIDATE_DIRS = _unique


def _compute_part_centroid(shape):
    """Compute the centroid (center of mass) of a shape."""
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    if props.Mass() < 1e-12:
        from OCC.Core.BRepGProp import brepgprop as bg
        props2 = GProp_GProps()
        bg.SurfaceProperties(shape, props2)
        c = props2.CentreOfMass()
        return np.array([c.X(), c.Y(), c.Z()])
    c = props.CentreOfMass()
    return np.array([c.X(), c.Y(), c.Z()])


def _compute_centroids(parts):
    """Compute centroids for all parts. Returns dict: name -> ndarray(3)."""
    centroids = {}
    for p in parts:
        try:
            centroids[p["name"]] = _compute_part_centroid(p["shape"])
        except Exception:
            centroids[p["name"]] = np.array([0.0, 0.0, 0.0])
    return centroids


def _bbox_shortest_axis_direction(part_name, parts):
    """
    Compute direction along the bounding-box shortest axis.
    Mating faces are typically perpendicular to the shortest dimension.
    Uses Bnd_Box directly — no mesh conversion needed.
    """
    for p in parts:
        if p["name"] == part_name:
            bbox = Bnd_Box()
            brepbndlib.Add(p["shape"], bbox)
            if bbox.IsVoid():
                return None
            xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
            extents = [xmax - xmin, ymax - ymin, zmax - zmin]
            axis_idx = int(np.argmin(extents))
            direction = [0.0, 0.0, 0.0]
            direction[axis_idx] = 1.0
            return direction
    return None


def _weighted_direction_search(primary_normal, centroid_dir=None,
                               gravity_bias=1.5):
    """
    Project the primary normal onto candidate directions and pick the best.

    Scoring: dot(primary_normal, candidate) * gravity_factor
    gravity_factor = 1.0 + gravity_bias * max(0, candidate.y)
    This preferentially selects directions with upward (+Y) component.
    """
    primary = np.array(primary_normal, dtype=np.float64)
    pnorm = np.linalg.norm(primary)
    if pnorm < 1e-10:
        primary = np.array([0.0, 1.0, 0.0])
    else:
        primary = primary / pnorm

    best_score = -1e10
    best_dir = np.array([0.0, 1.0, 0.0])

    for cand in CANDIDATE_DIRS:
        alignment = float(np.dot(primary, cand))
        if alignment < 0:
            continue

        gravity_factor = 1.0 + gravity_bias * max(0.0, float(cand[1]))
        score = alignment * gravity_factor

        if centroid_dir is not None:
            cnorm = np.linalg.norm(centroid_dir)
            if cnorm > 1e-10:
                c_hat = centroid_dir / cnorm
                centroid_alignment = float(np.dot(c_hat, cand))
                if centroid_alignment > 0:
                    score += 0.2 * centroid_alignment

        if score > best_score:
            best_score = score
            best_dir = cand.copy()

    return best_dir.tolist()


def calc_disassembly_direction(part_name, contacts, parts,
                               centroids=None):
    """
    Calculate the disassembly direction for a given part.

    Engineering-aware algorithm:
    1. Collect all contacts for this part, sorted by contact area (descending)
    2. Pick the largest-area contact's normal as the primary direction
    3. Area-weight the top contacts to refine direction
    4. Project onto 26 candidate axes with gravity bias
    5. Include parent centroid direction as a hint
    6. Fallback: bbox shortest axis, then parent centroid, then +Y

    Args:
        part_name: name of the part to compute direction for.
        contacts: list of contact dicts from detect_contacts().
        parts: list of part dicts with 'name' and 'shape'.
        centroids: optional pre-computed dict of name -> ndarray(3).

    Returns:
        list[float]: [x, y, z] unit direction vector.
    """
    my_contacts = []
    for c in contacts:
        if c["partA"] == part_name:
            normal = [-c["avgNormal"][0], -c["avgNormal"][1], -c["avgNormal"][2]]
            area = c.get("contactArea", 1.0)
            partner = c["partB"]
            my_contacts.append({"normal": normal, "area": area, "partner": partner})
        elif c["partB"] == part_name:
            normal = c["avgNormal"][:]
            area = c.get("contactArea", 1.0)
            partner = c["partA"]
            my_contacts.append({"normal": normal, "area": area, "partner": partner})

    if my_contacts:
        my_contacts.sort(key=lambda x: x["area"], reverse=True)

        top = my_contacts[:max(3, len(my_contacts))]
        total_area = sum(c["area"] for c in top)
        if total_area < 1e-10:
            total_area = 1.0

        weighted_normal = np.zeros(3)
        for c in top:
            weight = c["area"] / total_area
            weighted_normal += weight * np.array(c["normal"])

        wnorm = np.linalg.norm(weighted_normal)
        if wnorm > 1e-10:
            primary_normal = (weighted_normal / wnorm).tolist()
        else:
            largest = my_contacts[0]["normal"]
            ln = np.linalg.norm(largest)
            if ln > 1e-10:
                primary_normal = (np.array(largest) / ln).tolist()
            else:
                primary_normal = [0.0, 1.0, 0.0]

        parent_dir = _get_parent_centroid_direction(part_name, parts, centroids)

        return _weighted_direction_search(primary_normal, parent_dir)

    parent_dir = _get_parent_centroid_direction(part_name, parts, centroids)
    if parent_dir is not None:
        pnorm = np.linalg.norm(parent_dir)
        if pnorm > 1e-10:
            p_hat = (parent_dir / pnorm).tolist()
            return _weighted_direction_search(p_hat, parent_dir)

    bbox_dir = _bbox_shortest_axis_direction(part_name, parts)
    if bbox_dir is not None:
        return _weighted_direction_search(bbox_dir, parent_dir)

    return [0.0, 1.0, 0.0]


def _get_parent_centroid_direction(part_name, parts, centroids=None):
    """
    Compute direction from this part's centroid to its parent's centroid.
    Returns ndarray(3) or None.
    """
    part_entry = None
    for p in parts:
        if p["name"] == part_name:
            part_entry = p
            break

    if part_entry is None:
        return None

    parent_name = part_entry.get("parent")
    if not parent_name:
        return None

    if centroids is None:
        centroids = _compute_centroids(parts)

    my_c = centroids.get(part_name)
    parent_c = centroids.get(parent_name)
    if my_c is None or parent_c is None:
        return None

    diff = parent_c - my_c
    if np.linalg.norm(diff) < 1e-10:
        return None

    return diff


def compute_all_directions(parts, contacts):
    """
    Compute disassembly directions for all parts.

    Returns:
        dict[str, list[float]]: part_name -> [x, y, z] unit vector
    """
    centroids = _compute_centroids(parts)
    directions = {}
    for part in parts:
        name = part["name"]
        directions[name] = calc_disassembly_direction(
            name, contacts, parts, centroids)
    return directions
