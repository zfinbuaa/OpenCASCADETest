"""
Disassembly direction calculator — centroid-outward version.

Computes the preferred removal direction for each part based on:
1. Outward direction from assembly centroid to part centroid
2. Projection onto 26 candidate directions (6 axes + 8 body diagonals + 12 face diagonals)
3. Sibling repulsion: prefer directions away from sibling parts under the same parent
4. Fallback: bounding-box longest axis (insertion direction reversed)

No longer uses unreliable BRepExtrema avgNormal or gravity bias.
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
    try:
        props = GProp_GProps()
        brepgprop.VolumeProperties(shape, props)
        if props.Mass() > 1e-12:
            c = props.CentreOfMass()
            return np.array([c.X(), c.Y(), c.Z()])
    except Exception:
        pass
    try:
        props = GProp_GProps()
        brepgprop.SurfaceProperties(shape, props)
        c = props.CentreOfMass()
        return np.array([c.X(), c.Y(), c.Z()])
    except Exception:
        return np.array([0.0, 0.0, 0.0])


def _compute_part_volume(shape):
    """Compute volume of a shape."""
    try:
        props = GProp_GProps()
        brepgprop.VolumeProperties(shape, props)
        return props.Mass()
    except Exception:
        return 0.0


def _compute_centroids(parts):
    """Compute world-space centroids for all parts. Returns dict: name -> ndarray(3)."""
    centroids = {}
    for p in parts:
        c = _compute_part_centroid(p["shape"])
        if c is not None and p.get("transform"):
            mat = np.array(p["transform"], dtype=np.float64).reshape(4, 4, order='F')
            c_h = np.array([c[0], c[1], c[2], 1.0], dtype=np.float64)
            c = (mat @ c_h)[:3]
        centroids[p["name"]] = c
    return centroids


def _compute_assembly_centroid(parts, centroids=None):
    """
    Compute the volume-weighted centroid of the entire assembly.
    """
    if centroids is None:
        centroids = _compute_centroids(parts)

    weighted_sum = np.zeros(3)
    total_vol = 0.0
    for p in parts:
        vol = _compute_part_volume(p["shape"])
        c = centroids.get(p["name"], np.zeros(3))
        weighted_sum += vol * c
        total_vol += vol

    if total_vol > 1e-12:
        return weighted_sum / total_vol
    return np.mean(list(centroids.values()), axis=0) if centroids else np.zeros(3)


def _project_to_candidates(direction):
    """
    Project a direction vector onto the nearest 26 candidate direction.
    Returns the candidate direction as a list [x, y, z].
    """
    d = np.array(direction, dtype=np.float64)
    norm = np.linalg.norm(d)
    if norm < 1e-10:
        return [0.0, 1.0, 0.0]
    d = d / norm

    best_dot = -2.0
    best_dir = np.array([0.0, 1.0, 0.0])

    for cand in CANDIDATE_DIRS:
        dot = float(np.dot(d, cand))
        if dot > best_dot:
            best_dot = dot
            best_dir = cand

    return best_dir.tolist()


def _bbox_longest_axis_direction(part_name, parts):
    """
    Compute direction along the bounding-box longest axis.
    Parts are typically inserted along their longest dimension,
    so the removal direction is along that axis.
    """
    for p in parts:
        if p["name"] == part_name:
            bbox = Bnd_Box()
            brepbndlib.Add(p["shape"], bbox)
            if bbox.IsVoid():
                return None
            xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
            extents = [xmax - xmin, ymax - ymin, zmax - zmin]
            axis_idx = int(np.argmax(extents))
            direction = [0.0, 0.0, 0.0]
            direction[axis_idx] = 1.0
            return direction
    return None


def _compute_sibling_repulsion(part_name, parts, centroids):
    """
    Compute a direction that moves this part away from its siblings
    (other parts under the same parent sub-assembly).

    Returns ndarray(3) repulsion direction, or None if no siblings.
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

    siblings = []
    for p in parts:
        if p["name"] != part_name and p.get("parent") == parent_name:
            c = centroids.get(p["name"])
            if c is not None:
                siblings.append(c)

    if not siblings:
        return None

    my_c = centroids.get(part_name)
    if my_c is None:
        return None

    sibling_center = np.mean(siblings, axis=0)
    repulsion = my_c - sibling_center
    norm = np.linalg.norm(repulsion)
    if norm < 1e-10:
        return None
    return repulsion / norm


def calc_disassembly_direction(part_name, parts, centroids=None,
                                assembly_centroid=None, sub_assemblies=None):
    """
    Calculate the disassembly direction for a given part.

    Algorithm:
    1. Compute outward direction = normalize(part_centroid - assembly_centroid)
    2. Modify with sibling repulsion (move away from sibling cluster)
    3. Project onto 26 candidate directions
    4. Fallback: bbox longest axis, then +Y

    Args:
        part_name: name of the part.
        parts: list of part dicts with 'name', 'shape', 'parent', 'ancestors'.
        centroids: optional pre-computed centroids dict.
        assembly_centroid: optional pre-computed assembly centroid.
        sub_assemblies: optional list of sub-assembly dicts (for hierarchy).

    Returns:
        list[float]: [x, y, z] unit direction vector.
    """
    if centroids is None:
        centroids = _compute_centroids(parts)
    if assembly_centroid is None:
        assembly_centroid = _compute_assembly_centroid(parts, centroids)

    part_c = centroids.get(part_name)
    if part_c is None:
        return [0.0, 1.0, 0.0]

    outward = part_c - assembly_centroid
    outward_norm = np.linalg.norm(outward)

    if outward_norm < 1e-10:
        bbox_dir = _bbox_longest_axis_direction(part_name, parts)
        if bbox_dir is not None:
            return _project_to_candidates(bbox_dir)
        return [0.0, 1.0, 0.0]

    outward_hat = outward / outward_norm

    sibling_rep = _compute_sibling_repulsion(part_name, parts, centroids)
    if sibling_rep is not None:
        combined = 0.7 * outward_hat + 0.3 * sibling_rep
        combined_norm = np.linalg.norm(combined)
        if combined_norm > 1e-10:
            outward_hat = combined / combined_norm

    return _project_to_candidates(outward_hat)


def _default_direction(part_name, parts, centroids, sub_assemblies=None):
    """Default direction for a part with no contacts (free part).

    If the part has a parent sub-assembly, direction is along the line
    from the parent's centroid to the part's centroid. Otherwise +Y.
    """
    part_c = centroids.get(part_name)
    if part_c is None:
        return [0.0, 1.0, 0.0]

    parent_name = None
    for p in parts:
        if p["name"] == part_name:
            parent_name = p.get("parent")
            break

    if parent_name and sub_assemblies:
        for sa in sub_assemblies:
            if sa["name"] == parent_name:
                sa_centroid = sa.get("centroid")
                if sa_centroid is not None:
                    sa_c = np.array(sa_centroid)
                else:
                    sibling_centroids = []
                    for p in parts:
                        if p.get("parent") == parent_name and p["name"] != part_name:
                            c = centroids.get(p["name"])
                            if c is not None:
                                sibling_centroids.append(c)
                    if sibling_centroids:
                        sa_c = np.mean(sibling_centroids, axis=0)
                    else:
                        sa_c = None
                if sa_c is not None:
                    direction = part_c - sa_c
                    norm = np.linalg.norm(direction)
                    if norm > 1e-10:
                        return _project_to_candidates(direction / norm)
                break

    return [0.0, 1.0, 0.0]


def compute_all_directions(parts, contacts=None, sub_assemblies=None):
    """
    Compute disassembly directions for all parts using centroid-outward method.

    Parts with zero contacts are given a default direction (parent centroid
    to part centroid), skipping expensive sibling repulsion computation.

    Args:
        parts: list of part dicts with 'name', 'shape', 'parent'.
        contacts: optional list of contact dicts (used to identify free parts).
        sub_assemblies: optional list of sub-assembly dicts.

    Returns:
        dict[str, list[float]]: part_name -> [x, y, z] unit vector
    """
    centroids = _compute_centroids(parts)
    assembly_centroid = _compute_assembly_centroid(parts, centroids)

    contact_parts = set()
    if contacts:
        for c in contacts:
            contact_parts.add(c.get("partA", ""))
            contact_parts.add(c.get("partB", ""))

    directions = {}
    for part in parts:
        name = part["name"]
        if contacts and name not in contact_parts:
            directions[name] = _default_direction(
                name, parts, centroids, sub_assemblies)
        else:
            directions[name] = calc_disassembly_direction(
                name, parts, centroids, assembly_centroid, sub_assemblies)

    return directions
