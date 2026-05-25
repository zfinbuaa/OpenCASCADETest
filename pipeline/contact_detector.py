"""
Assembly contact detection - finds face-to-face contacts between parts.

Uses AABB spatial pre-filtering with optional mesh-based tight re-filter,
then ThreadPoolExecutor-parallel BRepExtrema for fast multi-core contact
detection on large assemblies.
"""

import sys
import os
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _aabb_overlap_local(a_min, a_max, b_min, b_max):
    """Local AABB overlap check (same as collision_check's version)."""
    return bool(np.all(a_min <= b_max) and np.all(a_max >= b_min))


def _mesh_proximity_check(cd_a, cd_b, threshold=CONTACT_THRESHOLD):
    """Check if two meshes have triangles within threshold distance.

    Uses the pre-built AABB trees from MeshCollisionData to find any
    triangle pair whose bounding boxes (expanded by threshold) overlap.
    Completely replaces BRepExtrema — no OCCT calls, thread-safe.

    Returns (min_dist, contact_points, normals) or None.
    """
    if cd_a is None or cd_b is None:
        return None
    if cd_a.tree is None or cd_b.tree is None:
        return None

    # Quick expanded-AABB check
    a_min = cd_a.aabb_min - threshold
    a_max = cd_a.aabb_max + threshold
    b_min = cd_b.aabb_min - threshold
    b_max = cd_b.aabb_max + threshold
    if not _aabb_overlap_local(a_min, a_max, b_min, b_max):
        return None

    contact_pairs = []

    # Iterative stack-based traversal (avoids Python recursion limit)
    stack = [(cd_a.tree, cd_b.tree)]
    while stack:
        na, nb = stack.pop()

        namin = na.min_v - threshold
        namax = na.max_v + threshold
        nbmin = nb.min_v - threshold
        nbmax = nb.max_v + threshold
        if not _aabb_overlap_local(namin, namax, nbmin, nbmax):
            continue

        if na.is_leaf and nb.is_leaf:
            for ia in na.tri_indices:
                ta = cd_a.triangles[ia]
                tva0 = cd_a.vertices[ta[0]]
                tva1 = cd_a.vertices[ta[1]]
                tva2 = cd_a.vertices[ta[2]]
                ta_min = np.minimum(np.minimum(tva0, tva1), tva2) - threshold
                ta_max = np.maximum(np.maximum(tva0, tva1), tva2) + threshold
                ca = (tva0 + tva1 + tva2) / 3.0

                for ib in nb.tri_indices:
                    tb = cd_b.triangles[ib]
                    tvb0 = cd_b.vertices[tb[0]]
                    tvb1 = cd_b.vertices[tb[1]]
                    tvb2 = cd_b.vertices[tb[2]]
                    tb_min = np.minimum(np.minimum(tvb0, tvb1), tvb2)
                    tb_max = np.maximum(np.maximum(tvb0, tvb1), tvb2)

                    if _aabb_overlap_local(ta_min, ta_max, tb_min, tb_max):
                        cb = (tvb0 + tvb1 + tvb2) / 3.0
                        d = float(np.linalg.norm(cb - ca))
                        if d < threshold * 5.0:
                            contact_pairs.append((ca, cb, d))
            continue

        if na.is_leaf:
            stack.append((na, nb.left))
            stack.append((na, nb.right))
        elif nb.is_leaf:
            stack.append((na.left, nb))
            stack.append((na.right, nb))
        else:
            stack.append((na.left, nb.left))
            stack.append((na.left, nb.right))
            stack.append((na.right, nb.left))
            stack.append((na.right, nb.right))

    if not contact_pairs:
        return None

    distances = np.array([p[2] for p in contact_pairs])
    idx = np.argsort(distances)

    top_pairs = [contact_pairs[i] for i in idx[:min(20, len(idx))]]
    min_dist = float(distances[idx[0]])

    contact_points = []
    normals = []
    for ca, cb, _ in top_pairs:
        contact_points.append(ca.tolist())
        vec = cb - ca
        n = float(np.linalg.norm(vec))
        normals.append((vec / n).tolist() if n > 1e-10 else [0.0, 0.0, 1.0])

    return min_dist, contact_points, normals


def _check_pairs_parallel(pairs, cd_list, max_workers=None):
    """Run _mesh_proximity_check on multiple pairs in parallel via ThreadPoolExecutor.

    Mesh operations are pure numpy/C — no OCCT, no GIL — fully thread-safe.
    """
    results = []
    total = len(pairs)
    if total == 0:
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {}
        for idx, (i, j) in enumerate(pairs):
            future = ex.submit(
                _mesh_proximity_check,
                cd_list[i], cd_list[j], CONTACT_THRESHOLD)
            futures[future] = (i, j, idx)

        processed = 0
        for future in as_completed(futures):
            i, j, idx = futures[future]
            processed += 1
            if processed % 50 == 0 or processed == total:
                sys.stdout.write("\r    parallel {}/{}  ".format(processed, total))
                sys.stdout.flush()
            try:
                result = future.result(timeout=300)
                if result is not None:
                    results.append((i, j, result))
            except Exception:
                pass

    sys.stdout.write("\n")
    sys.stdout.flush()
    return results


def _build_cd_name_map(parts, collision_data):
    """Build collision_data index list keyed by part position index."""
    if not collision_data:
        return None
    cd_list = [None] * len(parts)
    for i, p in enumerate(parts):
        cd_list[i] = collision_data.get(p["name"])
    return cd_list


def detect_contacts(parts, progress_callback=None, intra_parent_only=False,
                    collision_data=None, parallel=True):
    """
    Detect contact relationships using mesh AABB tree proximity checks.

    Pipeline:
      1. AABB sweep (coarse) → candidate pairs
      2. Mesh AABB tight re-filter (if collision_data provided)
      3. Mesh proximity check (AABB tree stack traversal, thread-safe)
         in parallel via ThreadPoolExecutor

    Args:
        parts: list of dicts with 'name', 'shape', 'parent'.
        progress_callback: optional callback(done, total, pair_name).
        intra_parent_only: only check contacts within same parent group.
        collision_data: pre-computed MeshCollisionData dict (required for mesh mode).
        parallel: use ThreadPoolExecutor (mesh ops are thread-safe).

    Returns:
        list[dict] with partA, partB, contactPoints, avgNormal,
                   minDistance, contactArea.
    """
    n = len(parts)
    max_workers = max(1, (os.cpu_count() or 4))
    cd_list = _build_cd_name_map(parts, collision_data)

    if intra_parent_only:
        groups = {}
        for i, p in enumerate(parts):
            parent_name = p.get("parent", "__root__")
            groups.setdefault(parent_name, []).append((i, p))

        contacts = []
        total_aabb = 0
        total_tight = 0

        for parent, idx_parts in groups.items():
            if len(idx_parts) < 2:
                continue

            indices = [ip[0] for ip in idx_parts]
            group_parts = [ip[1] for ip in idx_parts]
            local_cd = [cd_list[i] for i in indices] if cd_list else None

            aabbs = [_compute_aabb(p["shape"]) for p in group_parts]
            candidate_pairs = _find_overlap_pairs(aabbs)
            total_aabb += len(candidate_pairs)
            if not candidate_pairs:
                continue

            if local_cd:
                tight_pairs = []
                for i, j in candidate_pairs:
                    if _aabb_overlap_local(
                        local_cd[i].aabb_min - CONTACT_THRESHOLD,
                        local_cd[i].aabb_max + CONTACT_THRESHOLD,
                        local_cd[j].aabb_min - CONTACT_THRESHOLD,
                        local_cd[j].aabb_max + CONTACT_THRESHOLD
                    ) if local_cd[i] and local_cd[j] and local_cd[i].tree and local_cd[j].tree else True:
                        tight_pairs.append((i, j))
                total_tight += len(tight_pairs)
            else:
                tight_pairs = list(candidate_pairs)

            if parallel and len(tight_pairs) > 10 and local_cd:
                pair_results = _check_pairs_parallel(
                    tight_pairs, local_cd, max_workers)
            else:
                pair_results = []
                done = 0
                for i, j in tight_pairs:
                    done += 1
                    if done % 20 == 0 and len(tight_pairs) > 0:
                        sys.stdout.write("\r    serial {}/{}".format(
                            done, len(tight_pairs)))
                        sys.stdout.flush()
                    if local_cd:
                        r = _mesh_proximity_check(
                            local_cd[i], local_cd[j], CONTACT_THRESHOLD)
                    else:
                        r = None
                    if r is not None:
                        pair_results.append((i, j, r))
                if len(tight_pairs) > 0:
                    sys.stdout.write("\n")
                    sys.stdout.flush()

            for i, j, (min_dist, contact_points, normals) in pair_results:
                contact_area = _estimate_contact_area(contact_points, normals)
                avg_normal = _compute_avg_normal(normals)
                contacts.append({
                    "partA": group_parts[i]["name"],
                    "partB": group_parts[j]["name"],
                    "contactPoints": [cp[:3] for cp in contact_points],
                    "avgNormal": avg_normal,
                    "minDistance": min_dist,
                    "contactArea": contact_area,
                })

        sys.stdout.write("  {} AABB pairs ({} groups)".format(total_aabb, len(groups)))
        if collision_data:
            sys.stdout.write(", {} after mesh filter".format(total_tight))
        sys.stdout.write("\n  {} contact pairs\n".format(len(contacts)))
        sys.stdout.flush()
        return contacts

    # ── Default: global all-pairs check ──
    contacts = []
    aabbs = [_compute_aabb(part["shape"]) for part in parts]
    candidate_pairs = _find_overlap_pairs(aabbs)

    if cd_list:
        tight_pairs = []
        for i, j in candidate_pairs:
            if (cd_list[i] and cd_list[j] and cd_list[i].tree and cd_list[j].tree and
                _aabb_overlap_local(
                    cd_list[i].aabb_min - CONTACT_THRESHOLD,
                    cd_list[i].aabb_max + CONTACT_THRESHOLD,
                    cd_list[j].aabb_min - CONTACT_THRESHOLD,
                    cd_list[j].aabb_max + CONTACT_THRESHOLD)):
                tight_pairs.append((i, j))
    else:
        tight_pairs = list(candidate_pairs)

    sys.stdout.write("  {} AABB pairs (of {} total)".format(
        len(candidate_pairs), n * (n - 1) // 2))
    if cd_list:
        sys.stdout.write(", {} after mesh filter".format(len(tight_pairs)))
    sys.stdout.write("\n")
    sys.stdout.flush()

    if parallel and len(tight_pairs) > 10 and cd_list:
        pair_results = _check_pairs_parallel(tight_pairs, cd_list, max_workers)
    else:
        pair_results = []
        done = 0
        for i, j in tight_pairs:
            done += 1
            cd_a = cd_list[i] if cd_list else None
            cd_b = cd_list[j] if cd_list else None
            r = _mesh_proximity_check(cd_a, cd_b, CONTACT_THRESHOLD)
            if r is not None:
                pair_results.append((i, j, r))

    for i, j, (min_dist, contact_points, normals) in pair_results:
        avg_normal = _compute_avg_normal(normals)
        contact_area = _estimate_contact_area(contact_points, normals)
        contacts.append({
            "partA": parts[i]["name"],
            "partB": parts[j]["name"],
            "contactPoints": [cp[:3] for cp in contact_points],
            "avgNormal": avg_normal,
            "minDistance": min_dist,
            "contactArea": contact_area,
        })

    sys.stdout.write("  {} contact pairs\n".format(len(contacts)))
    sys.stdout.flush()
    return contacts

    # ── Default: global all-pairs check ──
    contacts = []
    aabbs = [_compute_aabb(part["shape"]) for part in parts]
    obbs = [_compute_obb(part["shape"]) for part in parts]

    candidate_pairs = _find_overlap_pairs(aabbs)
    total_pairs = len(candidate_pairs)

    sys.stdout.write("  {} candidate pairs after AABB filter (of {} total)\n".format(
        total_pairs, n * (n - 1) // 2))
    sys.stdout.flush()

    done = 0
    aabb_only_count = 0

    for i, j in sorted(candidate_pairs):
        if obbs and obbs[i] is not None and obbs[j] is not None:
            if not _obb_overlap_part(obbs[i], obbs[j]):
                continue

        aabb_only_count += 1
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
        avg_normal = _compute_avg_normal(normals)
        contact_area = _estimate_contact_area(contact_points, normals)

        contacts.append({
            "partA": name_a,
            "partB": name_b,
            "contactPoints": contact_points,
            "avgNormal": avg_normal,
            "minDistance": min_dist,
            "contactArea": contact_area,
        })

    sys.stdout.write("\n  {} OBB-filtered pairs checked\n".format(aabb_only_count))
    sys.stdout.flush()
    return contacts


def _compute_avg_normal(normals):
    """Compute average normal from a list of normals."""
    avg_normal = np.mean(normals, axis=0).tolist()
    length = (avg_normal[0] ** 2 + avg_normal[1] ** 2 + avg_normal[2] ** 2) ** 0.5
    if length > 1e-10:
        return [avg_normal[0] / length,
                avg_normal[1] / length,
                avg_normal[2] / length]
    return avg_normal


def get_contact_graph(contacts):
    """Build adjacency: part_name -> set of contacting part names."""
    graph = {}
    for c in contacts:
        a, b = c["partA"], c["partB"]
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
    return graph
