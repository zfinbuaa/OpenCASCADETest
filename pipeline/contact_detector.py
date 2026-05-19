"""
Assembly contact detection - finds face-to-face contacts between parts.

Uses AABB spatial pre-filtering before expensive BRep distance computation.
Includes contact area estimation for engineering-aware direction calculation.
"""

import sys
import numpy as np
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib


CONTACT_THRESHOLD = 0.1
AABB_PADDING = CONTACT_THRESHOLD * 2


def _compute_aabb(shape):
    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    return [xmin, ymin, zmin, xmax, ymax, zmax]


def _aabbs_overlap(a, b):
    return (
        a[0] - AABB_PADDING <= b[3] + AABB_PADDING and
        a[3] + AABB_PADDING >= b[0] - AABB_PADDING and
        a[1] - AABB_PADDING <= b[4] + AABB_PADDING and
        a[4] + AABB_PADDING >= b[1] - AABB_PADDING and
        a[2] - AABB_PADDING <= b[5] + AABB_PADDING and
        a[5] + AABB_PADDING >= b[2] - AABB_PADDING
    )


def _find_overlap_pairs(aabbs):
    """
    Find all AABB-overlapping pairs using sorted-sweep on the X axis
    then verifying Y and Z overlap. Returns set of (i,j) with i < j.
    """
    n = len(aabbs)
    x_sorted = sorted(range(n), key=lambda i: aabbs[i][0] - AABB_PADDING)
    pairs = set()

    for pos in range(n):
        i = x_sorted[pos]
        ax_min = aabbs[i][0] - AABB_PADDING
        ax_max = aabbs[i][3] + AABB_PADDING
        for scan in range(pos + 1, n):
            j = x_sorted[scan]
            jx_min = aabbs[j][0] - AABB_PADDING
            if jx_min > ax_max:
                break
            if _aabbs_overlap(aabbs[i], aabbs[j]):
                pair = (min(i, j), max(i, j))
                pairs.add(pair)

    return pairs


def _estimate_contact_area(contact_points, normals):
    """
    Estimate contact area from contact points using convex hull area
    on the best-fit plane.
    """
    if len(contact_points) < 3:
        if len(contact_points) == 2:
            p1 = np.array(contact_points[0])
            p2 = np.array(contact_points[1])
            return float(np.linalg.norm(p2 - p1) * 0.5)
        elif len(contact_points) == 1:
            return 0.5
        return 0.0

    pts = np.array(contact_points)
    centroid = pts.mean(axis=0)
    centered = pts - centroid

    if len(normals) > 0:
        avg_n = np.mean(normals, axis=0)
        norm = np.linalg.norm(avg_n)
        if norm > 1e-10:
            normal = avg_n / norm
        else:
            normal = np.array([0.0, 0.0, 1.0])
    else:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        normal = vh[-1]

    abs_n = np.abs(normal)
    drop_axis = np.argmax(abs_n)
    keep_axes = [i for i in range(3) if i != drop_axis]

    proj_2d = centered[:, keep_axes]

    area = _convex_hull_area_2d(proj_2d)
    return max(area, 0.1)


def _convex_hull_area_2d(points):
    """Compute convex hull area of 2D points using Graham scan."""
    pts = list(points)
    n = len(pts)
    if n < 3:
        if n == 2:
            dx = pts[1][0] - pts[0][0]
            dy = pts[1][1] - pts[0][1]
            return float(abs(dx * dy) * 0.25)
        return 0.0

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    pts_sorted = sorted(pts, key=lambda p: (p[0], p[1]))

    lower = []
    for p in pts_sorted:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper = []
    for p in reversed(pts_sorted):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    hull = lower[:-1] + upper[:-1]

    if len(hull) < 3:
        return 0.0

    area = 0.0
    for i in range(len(hull)):
        j = (i + 1) % len(hull)
        area += hull[i][0] * hull[j][1]
        area -= hull[j][0] * hull[i][1]
    return abs(area) / 2.0


def _check_pair_contact(shape_a, shape_b):
    """
    Run BRepExtrema distance check on a pair of shapes.

    Returns (min_distance, contact_points, normals) or None if no contact.
    """
    dist_calc = BRepExtrema_DistShapeShape(shape_a, shape_b)
    dist_calc.Perform()

    if not dist_calc.IsDone():
        return None

    min_dist = dist_calc.Value()
    if min_dist > CONTACT_THRESHOLD:
        return None

    contact_points = []
    normals = []
    nb_solutions = dist_calc.NbSolution()

    for k in range(1, nb_solutions + 1):
        p1 = dist_calc.PointOnShape1(k)
        p2 = dist_calc.PointOnShape2(k)
        d = p1.Distance(p2)
        if d <= CONTACT_THRESHOLD:
            contact_points.append([p1.X(), p1.Y(), p1.Z()])
            dx = p1.X() - p2.X()
            dy = p1.Y() - p2.Y()
            dz = p1.Z() - p2.Z()
            length = (dx * dx + dy * dy + dz * dz) ** 0.5
            if length > 1e-10:
                normals.append([dx / length, dy / length, dz / length])
            else:
                normals.append([0.0, 0.0, 1.0])

    if not contact_points:
        return None

    return min_dist, contact_points, normals


def detect_contacts(parts, progress_callback=None):
    """
    Detect contact relationships between all pairs of parts.

    Uses sorted-sweep AABB filtering to skip pairs that are far apart
    before running the expensive BRep distance computation.
    Also estimates contact area for each contact pair.

    Args:
        parts: list of dicts, each with 'name' and 'shape' (TopoDS_Shape).
        progress_callback: optional callback(done, total, pair_name).

    Returns:
        list[dict]: Each contact has partA, partB, contactPoints, avgNormal,
                    minDistance, contactArea.
    """
    contacts = []
    n = len(parts)

    aabbs = []
    for part in parts:
        aabbs.append(_compute_aabb(part["shape"]))

    candidate_pairs = _find_overlap_pairs(aabbs)
    total_pairs = len(candidate_pairs)

    sys.stdout.write("  {} candidate pairs after AABB filter (of {} total)\n".format(
        total_pairs, n * (n - 1) // 2))
    sys.stdout.flush()

    done = 0

    for i, j in sorted(candidate_pairs):
        shape_a = parts[i]["shape"]
        name_a = parts[i]["name"]
        shape_b = parts[j]["shape"]
        name_b = parts[j]["name"]

        done += 1
        if done % 20 == 0 or done == total_pairs:
            msg = "    pair {}/{}: {} <-> {}{}".format(
                done, total_pairs, name_a, name_b,
                " ..." if done < total_pairs else " done")
            sys.stdout.write("\r" + msg)
            sys.stdout.flush()
            if progress_callback:
                progress_callback(done, total_pairs,
                                  "{} <-> {}".format(name_a, name_b))

        result = _check_pair_contact(shape_a, shape_b)
        if result is None:
            continue

        min_dist, contact_points, normals = result

        avg_normal = np.mean(normals, axis=0).tolist()
        length = (avg_normal[0] ** 2 + avg_normal[1] ** 2 + avg_normal[2] ** 2) ** 0.5
        if length > 1e-10:
            avg_normal = [avg_normal[0] / length,
                          avg_normal[1] / length,
                          avg_normal[2] / length]

        contact_area = _estimate_contact_area(contact_points, normals)

        contacts.append({
            "partA": name_a,
            "partB": name_b,
            "contactPoints": contact_points,
            "avgNormal": avg_normal,
            "minDistance": min_dist,
            "contactArea": contact_area,
        })

    sys.stdout.write("\n")
    sys.stdout.flush()
    return contacts


def get_contact_graph(contacts):
    """Build adjacency: part_name -> set of contacting part names."""
    graph = {}
    for c in contacts:
        a, b = c["partA"], c["partB"]
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
    return graph
