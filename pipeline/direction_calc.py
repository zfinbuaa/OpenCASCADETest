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
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone


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


def _infer_constrained_direction(part_name, parts, contacts):
    """
    Infer the natural disassembly direction from contact surface geometry.

    Analyses the B-Rep face types near contact points to determine
    the implied assembly constraint:
      - Planar contact   →  face normal (perpendicular to mating surface)
      - Cylindrical face →  cylinder axis (bolt/shaft/pin removal direction)
      - Conical face     →  cone axis

    Returns:
        list[float]: [x, y, z] candidate direction, or None if inference fails.
    """
    contacts_for_part = [c for c in (contacts or [])
                         if c.get("partA") == part_name or c.get("partB") == part_name]
    if not contacts_for_part:
        return None

    part_shape = None
    for p in parts:
        if p["name"] == part_name:
            part_shape = p["shape"]
            break
    if part_shape is None:
        return None

    face_infos = []
    exp = TopExp_Explorer(part_shape, TopAbs_FACE)
    while exp.More():
        face = exp.Current()
        try:
            surf = BRepAdaptor_Surface(face)
            stype = surf.GetType()
            if stype == GeomAbs_Plane:
                plane = surf.Plane()
                ax = plane.Axis()
                d = ax.Direction()
                face_infos.append((np.array([d.X(), d.Y(), d.Z()]), 'plane'))
        except Exception:
            exp.Next()
            continue

        try:
            if stype == GeomAbs_Cylinder:
                cyl = surf.Cylinder()
                ax = cyl.Axis()
                d = ax.Direction()
                face_infos.append((np.array([d.X(), d.Y(), d.Z()]), 'cylinder'))
            elif stype == GeomAbs_Cone:
                cone = surf.Cone()
                ax = cone.Axis()
                d = ax.Direction()
                face_infos.append((np.array([d.X(), d.Y(), d.Z()]), 'cone'))
        except Exception:
            pass

        exp.Next()

    if not face_infos:
        return None

    has_cylindrical = any(fi[1] in ('cylinder', 'cone') for fi in face_infos)

    contact_normals = []
    contact_weights = []
    for c in contacts_for_part:
        normal = c.get("avgNormal", [0, 0, 1])
        area = c.get("contactArea", 0.0)
        n = np.array(normal, dtype=np.float64)
        norm_len = np.linalg.norm(n)
        if norm_len < 1e-10:
            continue
        contact_normals.append(n / norm_len)
        contact_weights.append(max(area, 0.01))

    if not contact_normals:
        return None

    avg_contact_normal = np.zeros(3)
    total_w = sum(contact_weights)
    for n, w in zip(contact_normals, contact_weights):
        avg_contact_normal += n * w
    if total_w > 1e-10:
        avg_contact_normal /= total_w

    if has_cylindrical:
        cyl_axes = [fi[0] for fi in face_infos if fi[1] in ('cylinder', 'cone')]
        if cyl_axes:
            avg_axis = np.zeros(3)
            for ax in cyl_axes:
                avg_axis += ax
            axis_norm = np.linalg.norm(avg_axis)
            if axis_norm > 1e-10:
                avg_axis /= axis_norm
                if np.dot(avg_contact_normal, avg_axis) > 0:
                    avg_axis = -avg_axis
                return _project_to_candidates(avg_axis)
            # Symmetric/opposing axes cancel out → fall through to contact normal

    return _project_to_candidates(avg_contact_normal)


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
                                assembly_centroid=None, sub_assemblies=None,
                                contacts=None):
    """
    Calculate the disassembly direction for a given part.

    Algorithm:
    1. Try constraint inference from contact face types
       (planar contact → face normal, cylindrical → axis direction)
    2. Fallback: hierarchy-aware direction (parent centroid → part centroid)
       with sibling repulsion
    3. Further fallback: centroid-outward with sibling repulsion
    4. Project onto 26 candidate directions
    5. Ultimate fallback: bbox longest axis, then +Y

    Args:
        part_name: name of the part.
        parts: list of part dicts with 'name', 'shape', 'parent', 'ancestors'.
        centroids: optional pre-computed centroids dict.
        assembly_centroid: optional pre-computed assembly centroid.
        sub_assemblies: optional list of sub-assembly dicts (for hierarchy).
        contacts: optional list of contact dicts for constraint inference.

    Returns:
        list[float]: [x, y, z] unit direction vector.
    """
    if contacts:
        constrained = _infer_constrained_direction(part_name, parts, contacts)
        if constrained is not None:
            return constrained

    if centroids is None:
        centroids = _compute_centroids(parts)
    if assembly_centroid is None:
        assembly_centroid = _compute_assembly_centroid(parts, centroids)

    part_c = centroids.get(part_name)
    if part_c is None:
        return [0.0, 1.0, 0.0]

    parent_name = None
    for p in parts:
        if p["name"] == part_name:
            parent_name = p.get("parent")
            break

    direction = None

    if parent_name and sub_assemblies:
        sub_sa_centroids = _compute_sub_assembly_centroids(sub_assemblies, centroids)
        parent_c = sub_sa_centroids.get(parent_name)
        if parent_c is not None:
            diff = part_c - parent_c
            norm = np.linalg.norm(diff)
            if norm > 1e-10:
                direction = diff / norm

    if direction is None:
        outward = part_c - assembly_centroid
        outward_norm = np.linalg.norm(outward)

        if outward_norm < 1e-10:
            bbox_dir = _bbox_longest_axis_direction(part_name, parts)
            if bbox_dir is not None:
                return _project_to_candidates(bbox_dir)
            return [0.0, 1.0, 0.0]

        direction = outward / outward_norm

    sibling_rep = _compute_sibling_repulsion(part_name, parts, centroids)
    if sibling_rep is not None:
        combined = 0.6 * direction + 0.4 * sibling_rep
        combined_norm = np.linalg.norm(combined)
        if combined_norm > 1e-10:
            direction = combined / combined_norm

    return _project_to_candidates(direction)


def compute_all_directions(parts, contacts=None, sub_assemblies=None):
    """
    Compute disassembly directions for all parts using constraint-inference
    first, then hierarchy-aware centroid-outward as fallback.

    For parts with contacts, analyses face types to infer natural
    disassembly direction (planar → face normal, cylindrical → axis).
    Parts without contacts use a hierarchy-aware direction:
      1. Direction from parent sub-assembly centroid to part centroid
      2. Fallback: centroid-outward with sibling repulsion

    Args:
        parts: list of part dicts with 'name', 'shape', 'parent'.
        contacts: optional list of contact dicts (used for constraint inference).
        sub_assemblies: optional list of sub-assembly dicts.

    Returns:
        dict[str, list[float]]: part_name -> [x, y, z] unit vector
    """
    centroids = _compute_centroids(parts)
    assembly_centroid = _compute_assembly_centroid(parts, centroids)

    sub_sa_centroids = _compute_sub_assembly_centroids(sub_assemblies, centroids)

    contact_parts = set()
    if contacts:
        for c in contacts:
            contact_parts.add(c.get("partA", ""))
            contact_parts.add(c.get("partB", ""))

    directions = {}
    for part in parts:
        name = part["name"]
        if contacts and name not in contact_parts:
            directions[name] = _hierarchy_aware_direction(
                name, parts, centroids, sub_assemblies, sub_sa_centroids,
                assembly_centroid)
        else:
            directions[name] = calc_disassembly_direction(
                name, parts, centroids, assembly_centroid, sub_assemblies,
                contacts=contacts)

    return directions


def _compute_sub_assembly_centroids(sub_assemblies, part_centroids):
    """
    Compute effective centroids for all sub-assembly nodes.

    For each sub-assembly, the centroid is either:
      - Pre-computed from the OCCT shape (if available), or
      - The mean of its descendant leaf part centroids.

    Returns:
        dict[str, ndarray(3)]: sub-assembly name -> centroid
    """
    if not sub_assemblies:
        return {}

    sa_centroids = {}
    for sa in sub_assemblies:
        if sa.get("centroid") is not None:
            sa_centroids[sa["name"]] = np.array(sa["centroid"])
        else:
            child_centroids = []
            for cn in sa.get("child_names", []):
                c = part_centroids.get(cn)
                if c is not None:
                    child_centroids.append(c)
            if child_centroids:
                sa_centroids[sa["name"]] = np.mean(child_centroids, axis=0)

    for sa in sub_assemblies:
        if sa["name"] not in sa_centroids:
            for cn in sa.get("child_names", []):
                if cn in sa_centroids and cn not in part_centroids:
                    sa_centroids[sa["name"]] = sa_centroids[cn]
                    break

    return sa_centroids


def _hierarchy_aware_direction(part_name, parts, centroids, sub_assemblies,
                                sub_sa_centroids, assembly_centroid):
    """
    Compute disassembly direction using hierarchy information.

    Priority:
      1. Direction from parent sub-assembly centroid to part centroid.
         This respects the assembly structure: parts move away from
         their parent assembly center.
      2. If the part is itself the outermost in the tree, use the
         direction from the overall assembly centroid.
      3. Combine with sibling repulsion for better separation.
      4. Project onto 26 candidate directions.

    Returns:
        list[float]: [x, y, z] unit direction vector.
    """
    part_c = centroids.get(part_name)
    if part_c is None:
        return [0.0, 1.0, 0.0]

    parent_name = None
    for p in parts:
        if p["name"] == part_name:
            parent_name = p.get("parent")
            break

    direction = None

    if parent_name and sub_assemblies:
        parent_c = sub_sa_centroids.get(parent_name)
        if parent_c is not None:
            diff = part_c - parent_c
            norm = np.linalg.norm(diff)
            if norm > 1e-10:
                direction = diff / norm

    if direction is None:
        outward = part_c - assembly_centroid
        norm = np.linalg.norm(outward)
        if norm > 1e-10:
            direction = outward / norm

    if direction is None:
        bbox_dir = _bbox_longest_axis_direction(part_name, parts)
        if bbox_dir is not None:
            return _project_to_candidates(bbox_dir)
        return [0.0, 1.0, 0.0]

    sibling_rep = _compute_sibling_repulsion(part_name, parts, centroids)
    if sibling_rep is not None:
        combined = 0.6 * direction + 0.4 * sibling_rep
        combined_norm = np.linalg.norm(combined)
        if combined_norm > 1e-10:
            direction = combined / combined_norm

    return _project_to_candidates(direction)
