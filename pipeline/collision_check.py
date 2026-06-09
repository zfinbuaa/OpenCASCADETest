"""
Swept collision detection along disassembly paths.

Optimized version using triangle-mesh-level collision detection with
AABB bounding-volume hierarchies and binary-search step refinement.

Includes direction search and AABB-level fast pre-filtering.
Falls back to the BRep boolean method when mesh data is unavailable.
"""

import logging
import sys
import os
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.gp import gp_Trsf, gp_Vec
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib

from pipeline.direction_calc import CANDIDATE_DIRS
from pipeline._occ_lock import OCC_BREP_LOCK

logger = logging.getLogger(__name__)


def _shape_to_mesh_arrays(shape, linear_deflection=1.0):
    """Convert a B-Rep shape to numpy vertex and triangle arrays."""
    from pipeline.mesher import brep_to_mesh
    try:
        verts, tris, _ = brep_to_mesh(shape, linear_deflection=linear_deflection)
        if len(verts) < 9 or len(tris) < 1:
            return None, None
        v = np.array(verts, dtype=np.float64).reshape(-1, 3)
        t = np.array(tris, dtype=np.int32)
        return v, t
    except Exception:
        return None, None


def _compute_aabb_np(vertices):
    """Compute AABB from vertex array. Returns (min_v, max_v) as ndarray(3)."""
    return vertices.min(axis=0), vertices.max(axis=0)


def _aabb_overlap_np(a_min, a_max, b_min, b_max):
    """Check if two AABBs overlap."""
    return bool(np.all(a_min <= b_max) and np.all(a_max >= b_min))


class _AABBNode:
    __slots__ = ('min_v', 'max_v', 'left', 'right', 'tri_indices', 'is_leaf')

    def __init__(self):
        self.min_v = None
        self.max_v = None
        self.left = None
        self.right = None
        self.tri_indices = None
        self.is_leaf = False


def _build_aabb_tree(vertices, triangles, max_leaf_size=8):
    """Build a simple AABB tree over triangles."""
    n_tris = len(triangles)
    if n_tris == 0:
        return None

    def compute_bbox(indices):
        all_v = []
        for idx in indices:
            tri = triangles[idx]
            all_v.append(vertices[tri[0]])
            all_v.append(vertices[tri[1]])
            all_v.append(vertices[tri[2]])
        arr = np.array(all_v)
        return arr.min(axis=0), arr.max(axis=0)

    def build(indices, depth=0):
        node = _AABBNode()
        node.min_v, node.max_v = compute_bbox(indices)

        if len(indices) <= max_leaf_size:
            node.is_leaf = True
            node.tri_indices = list(indices)
            return node

        extent = node.max_v - node.min_v
        axis = int(np.argmax(extent))

        centroids = []
        for idx in indices:
            tri = triangles[idx]
            c = (vertices[tri[0]] + vertices[tri[1]] + vertices[tri[2]]) / 3.0
            centroids.append(c[axis])
        centroids = np.array(centroids)
        median = np.median(centroids)

        left_indices = []
        right_indices = []
        for i, idx in enumerate(indices):
            if centroids[i] <= median:
                left_indices.append(idx)
            else:
                right_indices.append(idx)

        if not left_indices or not right_indices:
            node.is_leaf = True
            node.tri_indices = list(indices)
            return node

        node.left = build(left_indices, depth + 1)
        node.right = build(right_indices, depth + 1)
        return node

    return build(list(range(n_tris)))


def _triangles_overlap(v0, v1, v2, u0, u1, u2):
    """Fast triangle-triangle overlap test using separating axis theorem.

    Tests all 11 required axes: 2 face normals + 9 edge cross products.
    For coplanar triangles, falls back to 2D edge-edge intersection check.
    """
    e0 = v1 - v0
    e1 = v2 - v0
    f0 = u1 - u0
    f1 = u2 - u0

    n0 = np.cross(e0, e1)
    ln = np.dot(n0, n0)
    if ln < 1e-20:
        return False
    n0 /= ln ** 0.5

    d0 = np.dot(n0, v0)
    du0 = np.dot(n0, u0) - d0
    du1 = np.dot(n0, u1) - d0
    du2 = np.dot(n0, u2) - d0

    if du0 * du1 > 0 and du0 * du2 > 0 and du1 * du2 > 0:
        return False

    e_min = min(du0, du1, du2)
    e_max = max(du0, du1, du2)
    tol = (e_max - e_min) * 0.01
    if e_min > tol or e_max < -tol:
        return False

    n1 = np.cross(f0, f1)
    ln1 = np.dot(n1, n1)
    if ln1 < 1e-20:
        return False
    n1 /= ln1 ** 0.5

    coplanar = abs(np.dot(n0, n1)) > 0.9999

    if not coplanar:
        d1 = np.dot(n1, u0)
        dv0 = np.dot(n1, v0) - d1
        dv1 = np.dot(n1, v1) - d1
        dv2 = np.dot(n1, v2) - d1

        if dv0 * dv1 > 0 and dv0 * dv2 > 0 and dv1 * dv2 > 0:
            return False

        f_min = min(dv0, dv1, dv2)
        f_max = max(dv0, dv1, dv2)
        tol2 = (f_max - f_min) * 0.01
        if f_min > tol2 or f_max < -tol2:
            return False

    for edge_a in [e0, e1, v2 - v1]:
        for edge_b in [f0, f1, u2 - u1]:
            axis = np.cross(edge_a, edge_b)
            la2 = np.dot(axis, axis)
            if la2 < 1e-20:
                continue
            axis /= la2 ** 0.5

            a_vals = [np.dot(axis, v0), np.dot(axis, v1), np.dot(axis, v2)]
            b_vals = [np.dot(axis, u0), np.dot(axis, u1), np.dot(axis, u2)]

            if min(a_vals) > max(b_vals) + 1e-10:
                return False
            if min(b_vals) > max(a_vals) + 1e-10:
                return False

    if coplanar:
        return _coplanar_triangles_overlap_2d(v0, v1, v2, u0, u1, u2, n0)

    return True


def _coplanar_triangles_overlap_2d(v0, v1, v2, u0, u1, u2, normal):
    """Check if two coplanar triangles overlap via 2D edge-edge test.

    Projects onto the plane by dropping the axis with largest normal component,
    then checks for edge intersections and point containment.
    """
    abs_n = np.abs(normal)
    drop = int(np.argmax(abs_n))
    keep = [i for i in range(3) if i != drop]

    def proj(pt):
        return np.array([pt[keep[0]], pt[keep[1]]])

    a_pts = [proj(v0), proj(v1), proj(v2)]
    b_pts = [proj(u0), proj(u1), proj(u2)]

    edges_a = [(a_pts[0], a_pts[1]), (a_pts[1], a_pts[2]), (a_pts[2], a_pts[0])]
    edges_b = [(b_pts[0], b_pts[1]), (b_pts[1], b_pts[2]), (b_pts[2], b_pts[0])]

    for (sa, ea) in edges_a:
        for (sb, eb) in edges_b:
            if _segments_intersect_2d(sa, ea, sb, eb):
                return True

    if _point_in_triangle_2d(b_pts[0], a_pts[0], a_pts[1], a_pts[2]):
        return True
    if _point_in_triangle_2d(a_pts[0], b_pts[0], b_pts[1], b_pts[2]):
        return True

    return False


def _segments_intersect_2d(a, b, c, d):
    """Check if two 2D segments ab and cd intersect (including endpoints)."""
    def cross2d(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on_segment(p, q, r):
        return (min(p[0], q[0]) - 1e-10 <= r[0] <= max(p[0], q[0]) + 1e-10 and
                min(p[1], q[1]) - 1e-10 <= r[1] <= max(p[1], q[1]) + 1e-10)

    d1 = cross2d(c, d, a)
    d2 = cross2d(c, d, b)
    d3 = cross2d(a, b, c)
    d4 = cross2d(a, b, d)

    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return True

    if abs(d1) < 1e-10 and on_segment(c, d, a):
        return True
    if abs(d2) < 1e-10 and on_segment(c, d, b):
        return True
    if abs(d3) < 1e-10 and on_segment(a, b, c):
        return True
    if abs(d4) < 1e-10 and on_segment(a, b, d):
        return True

    return False


def _point_in_triangle_2d(pt, a, b, c):
    """Check if a 2D point is inside triangle abc using barycentric coordinates."""
    v0x, v0y = c[0] - a[0], c[1] - a[1]
    v1x, v1y = b[0] - a[0], b[1] - a[1]
    v2x, v2y = pt[0] - a[0], pt[1] - a[1]

    dot00 = v0x * v0x + v0y * v0y
    dot01 = v0x * v1x + v0y * v1y
    dot02 = v0x * v2x + v0y * v2y
    dot11 = v1x * v1x + v1y * v1y
    dot12 = v1x * v2x + v1y * v2y

    denom = dot00 * dot11 - dot01 * dot01
    if abs(denom) < 1e-20:
        return False

    inv = 1.0 / denom
    u = (dot11 * dot02 - dot01 * dot12) * inv
    v = (dot00 * dot12 - dot01 * dot02) * inv

    return u >= -1e-10 and v >= -1e-10 and (u + v) <= 1.0 + 1e-10


def _check_mesh_intersection(moved_verts, moved_tris, moved_tree,
                             moved_aabb_min, moved_aabb_max,
                             obs_verts, obs_tris, obs_tree,
                             obs_aabb_min, obs_aabb_max):
    """Check for triangle-level intersection between two mesh AABB trees."""
    if not _aabb_overlap_np(moved_aabb_min, moved_aabb_max,
                            obs_aabb_min, obs_aabb_max):
        return False

    if moved_tree is None or obs_tree is None:
        return False

    def traverse(node_a, node_b):
        if not _aabb_overlap_np(node_a.min_v, node_a.max_v,
                                node_b.min_v, node_b.max_v):
            return False

        a_leaf = node_a.is_leaf
        b_leaf = node_b.is_leaf

        if a_leaf and b_leaf:
            for ia in node_a.tri_indices:
                ta = moved_tris[ia]
                tv0 = moved_verts[ta[0]]
                tv1 = moved_verts[ta[1]]
                tv2 = moved_verts[ta[2]]
                for ib in node_b.tri_indices:
                    tb = obs_tris[ib]
                    if _triangles_overlap(tv0, tv1, tv2,
                                          obs_verts[tb[0]],
                                          obs_verts[tb[1]],
                                          obs_verts[tb[2]]):
                        return True
            return False

        if a_leaf:
            return (traverse(node_a, node_b.left) or
                    traverse(node_a, node_b.right))

        if b_leaf:
            return (traverse(node_a.left, node_b) or
                    traverse(node_a.right, node_b))

        children_a = [node_a.left, node_a.right]
        children_b = [node_b.left, node_b.right]
        for ca in children_a:
            for cb in children_b:
                if traverse(ca, cb):
                    return True
        return False

    return traverse(moved_tree, obs_tree)


class MeshCollisionData:
    """Pre-computed mesh data for fast collision checking."""

    def __init__(self, shape, linear_deflection=1.0):
        self.shape = shape
        self.vertices, self.triangles = _shape_to_mesh_arrays(
            shape, linear_deflection)
        if self.vertices is not None:
            self.tree = _build_aabb_tree(self.vertices, self.triangles)
            self.aabb_min, self.aabb_max = _compute_aabb_np(self.vertices)
        else:
            self.tree = None
            self.aabb_min = None
            self.aabb_max = None
        self.volume = _compute_volume(shape)


def prepare_collision_data(parts, linear_deflection=1.0):
    """Pre-compute mesh and AABB data for all parts in world space."""
    from pipeline.gltf_exporter import _apply_transform
    data = {}
    n = len(parts)
    for idx, part in enumerate(parts):
        name = part["name"]
        if n > 10 and (idx % 10 == 0 or idx == n - 1):
            sys.stdout.write("\r  meshing for collision: {}/{}...".format(idx + 1, n))
            sys.stdout.flush()
        cd = MeshCollisionData(part["shape"], linear_deflection)
        if cd.vertices is not None and part.get("transform"):
            cd.vertices = _apply_transform(cd.vertices, part["transform"])
            cd.aabb_min, cd.aabb_max = _compute_aabb_np(cd.vertices)
            cd.tree = _build_aabb_tree(cd.vertices, cd.triangles)
        data[name] = cd
    if n > 10:
        sys.stdout.write("\n")
        sys.stdout.flush()
    return data


def _compute_volume(shape):
    props = GProp_GProps()
    brepgprop.VolumeProperties(shape, props)
    return props.Mass()


def _has_interference_brep(moved_shape, obstacle_shape, moved_volume):
    """BRep boolean fallback for interference check."""
    if moved_volume < 1e-9:
        return False
    cut = BRepAlgoAPI_Cut(moved_shape, obstacle_shape)
    if not cut.IsDone():
        return False
    vol_cut = _compute_volume(cut.Shape())
    if vol_cut is None:
        return False
    ratio = (moved_volume - vol_cut) / moved_volume
    return ratio > 0.001


def check_disassembly_path(part_name, part_shape, other_shapes, direction,
                           max_distance=500.0, steps=20,
                           collision_data=None,
                           report_all_collisions=False):
    """
    Check if a part can move along a direction without colliding.

    Args:
        part_name: string name of the part (for collision_data lookup).
        part_shape: TopoDS_Shape of the part to move.
        other_shapes: list of (name, TopoDS_Shape) tuples for obstacles.
        direction: [x, y, z] unit vector for movement direction.
        max_distance: total distance to check (mm).
        steps: number of discrete check points along the path.
        collision_data: dict of name -> MeshCollisionData (optional).
        report_all_collisions: if True, collect ALL colliding obstacle names
            across all obstacles at the first collision step (instead of
            stopping at the first one). The result dict will contain
            "collision_names" (list[str]) instead of just "collision_with".

    Returns:
        dict with feasible, max_safe_distance, collision_at_step,
        collision_with (str or None), collision_names (list[str]), total_steps.
    """
    dir_np = np.array(direction, dtype=np.float64)

    if collision_data is not None:
        return _check_path_mesh(
            part_name, part_shape, other_shapes, dir_np,
            max_distance, steps, collision_data,
            report_all_collisions=report_all_collisions)

    return _check_path_brep(
        part_shape, other_shapes, dir_np,
        max_distance, steps,
        report_all_collisions=report_all_collisions)


def _translate_aabb_tree(node, offset):
    """Return a shallow clone of the AABB tree with all bboxes translated by offset."""
    if node is None:
        return None
    new_node = _AABBNode()
    new_node.min_v = node.min_v + offset
    new_node.max_v = node.max_v + offset
    new_node.is_leaf = node.is_leaf
    new_node.tri_indices = node.tri_indices
    if not node.is_leaf:
        new_node.left = _translate_aabb_tree(node.left, offset)
        new_node.right = _translate_aabb_tree(node.right, offset)
    return new_node


def _safe_coarse_step_size(part_aabb, obstacle_aabbs):
    min_extent = min(
        part_aabb[3] - part_aabb[0],
        part_aabb[4] - part_aabb[1],
        part_aabb[5] - part_aabb[2]
    )
    for ob in obstacle_aabbs:
        for lo, hi in [(ob[0], ob[3]), (ob[1], ob[4]), (ob[2], ob[5])]:
            e = hi - lo
            if e > 0:
                min_extent = min(min_extent, e)
    return max(0.5, min_extent * 0.5)


def _check_path_mesh(part_name, part_shape, other_shapes, dir_np,
                     max_distance, steps, collision_data,
                     report_all_collisions=False):
    """Mesh-based collision check with AABB pre-filter and binary search."""
    part_data = collision_data.get(part_name)

    if part_data is None or part_data.vertices is None or part_data.tree is None:
        return _check_path_brep(
            part_shape, other_shapes, dir_np.tolist(),
            max_distance, steps,
            report_all_collisions=report_all_collisions)

    obs_data_list = []
    for other_name, other_shape in other_shapes:
        od = collision_data.get(other_name)
        if od is None or od.vertices is None or od.tree is None:
            obs_data_list.append((other_name, None, None, None, None, None, other_shape))
        else:
            obs_data_list.append((other_name, od.vertices, od.triangles,
                                  od.tree, od.aabb_min, od.aabb_max, other_shape))

    base_tree = part_data.tree
    base_aabb_min = part_data.aabb_min
    base_aabb_max = part_data.aabb_max
    base_verts = part_data.vertices

    part_aabb = (
        float(base_aabb_min[0]), float(base_aabb_min[1]), float(base_aabb_min[2]),
        float(base_aabb_max[0]), float(base_aabb_max[1]), float(base_aabb_max[2]),
    )
    obstacle_aabbs = []
    for _, obs_v, obs_t, obs_tree, obs_amin, obs_amax, obs_shape in obs_data_list:
        if obs_amin is not None and obs_amax is not None:
            obstacle_aabbs.append((
                float(obs_amin[0]), float(obs_amin[1]), float(obs_amin[2]),
                float(obs_amax[0]), float(obs_amax[1]), float(obs_amax[2]),
            ))
    safe_step = _safe_coarse_step_size(part_aabb, obstacle_aabbs)
    coarse_step_size = safe_step
    coarse_steps = max(1, int(np.ceil(max_distance / coarse_step_size)))
    step_size = coarse_step_size

    collision_step = -1
    collision_name = None
    collision_names_set = set()
    last_safe_step = 0

    for step in range(1, coarse_steps + 1):
        dist = step * step_size
        offset = dir_np * dist

        moved_verts = base_verts + offset
        moved_tree = _translate_aabb_tree(base_tree, offset)
        moved_aabb_min = base_aabb_min + offset
        moved_aabb_max = base_aabb_max + offset

        step_hit = False
        for other_name, obs_v, obs_t, obs_tree, obs_amin, obs_amax, obs_shape in obs_data_list:
            hit_this = False
            if obs_tree is not None and obs_amin is not None:
                if _check_mesh_intersection(moved_verts, part_data.triangles,
                                            moved_tree, moved_aabb_min, moved_aabb_max,
                                            obs_v, obs_t, obs_tree,
                                            obs_amin, obs_amax):
                    hit_this = True
            else:
                with OCC_BREP_LOCK:
                    vec = gp_Vec(dir_np[0] * dist, dir_np[1] * dist, dir_np[2] * dist)
                    trsf = gp_Trsf()
                    trsf.SetTranslation(vec)
                    moved_shape = BRepBuilderAPI_Transform(part_shape, trsf).Shape()
                    interferes = _has_interference_brep(moved_shape, obs_shape, part_data.volume)
                if interferes:
                    hit_this = True

            if hit_this:
                step_hit = True
                if collision_name is None:
                    collision_name = other_name
                    collision_step = step
                collision_names_set.add(other_name)
                if not report_all_collisions:
                    break

        if step_hit and not report_all_collisions:
            break
        if step_hit and report_all_collisions:
            break
        last_safe_step = step

    if collision_step < 0:
        return {
            "feasible": True,
            "max_safe_distance": max_distance,
            "collision_at_step": -1,
            "collision_with": None,
            "collision_names": [],
            "total_steps": steps,
        }

    lo = (collision_step - 1) * step_size
    hi = collision_step * step_size

    for _ in range(5):
        mid = (lo + hi) / 2.0
        offset = dir_np * mid
        moved_verts = base_verts + offset
        moved_tree = _translate_aabb_tree(base_tree, offset)
        moved_aabb_min = base_aabb_min + offset
        moved_aabb_max = base_aabb_max + offset

        hit = False
        for other_name, obs_v, obs_t, obs_tree, obs_amin, obs_amax, obs_shape in obs_data_list:
            if obs_tree is not None and obs_amin is not None:
                if _check_mesh_intersection(moved_verts, part_data.triangles,
                                            moved_tree, moved_aabb_min, moved_aabb_max,
                                            obs_v, obs_t, obs_tree,
                                            obs_amin, obs_amax):
                    hit = True
                    break
            else:
                with OCC_BREP_LOCK:
                    vec = gp_Vec(dir_np[0] * mid, dir_np[1] * mid, dir_np[2] * mid)
                    trsf = gp_Trsf()
                    trsf.SetTranslation(vec)
                    moved_shape = BRepBuilderAPI_Transform(part_shape, trsf).Shape()
                    interferes = _has_interference_brep(moved_shape, obs_shape, part_data.volume)
                if interferes:
                    hit = True
                    break

        if hit:
            hi = mid
        else:
            lo = mid

    return {
        "feasible": False,
        "max_safe_distance": lo,
        "collision_at_step": collision_step,
        "collision_with": collision_name,
        "collision_names": sorted(collision_names_set),
        "total_steps": steps,
    }


def _check_path_brep(part_shape, other_shapes, direction,
                     max_distance, steps,
                     report_all_collisions=False):
    """Original BRep boolean collision check (fallback)."""
    step_size = max_distance / steps

    for step in range(1, steps + 1):
        dist = step * step_size
        vec = gp_Vec(direction[0] * dist,
                     direction[1] * dist,
                     direction[2] * dist)
        transform = gp_Trsf()
        transform.SetTranslation(vec)
        with OCC_BREP_LOCK:
            moved_shape = BRepBuilderAPI_Transform(part_shape, transform).Shape()

        vol_moved = _compute_volume(moved_shape)
        if vol_moved is None:
            continue

        collision_name = None
        collision_names_set = set()
        for other_name, other_shape in other_shapes:
            with OCC_BREP_LOCK:
                interferes = _has_interference_brep(moved_shape, other_shape, vol_moved)
            if interferes:
                if collision_name is None:
                    collision_name = other_name
                collision_names_set.add(other_name)
                if not report_all_collisions:
                    break

        if collision_name is not None:
            safe_dist = (step - 1) * step_size
            return {
                "feasible": False,
                "max_safe_distance": safe_dist,
                "collision_at_step": step,
                "collision_with": collision_name,
                "collision_names": sorted(collision_names_set),
                "total_steps": steps,
            }

    return {
        "feasible": True,
        "max_safe_distance": max_distance,
        "collision_at_step": -1,
        "collision_with": None,
        "collision_names": [],
        "total_steps": steps,
    }


def find_best_feasible_direction(part_name, part_shape, obstacle_shapes,
                                  preferred_dir, max_distance=500.0,
                                  collision_data=None):
    """
    Search for a feasible disassembly direction for a part.

    Checks all 26 candidate directions in parallel via ThreadPoolExecutor
    (mesh operations are thread-safe). Returns the first feasible direction
    found, or the one with the largest safe_distance if none feasible.

    Returns:
        tuple: (best_direction, check_result)
    """
    preferred = np.array(preferred_dir, dtype=np.float64)
    pnorm = np.linalg.norm(preferred)

    sorted_candidates = []
    for cand in CANDIDATE_DIRS:
        if pnorm > 1e-10:
            dot = float(np.dot(preferred / pnorm, cand))
        else:
            dot = 0.0
        sorted_candidates.append((dot, cand.tolist()))

    sorted_candidates.sort(key=lambda x: -x[0])

    best_result = None
    best_dir = None
    best_safe = -1.0
    feasible_found = False
    feasible_dir = None
    feasible_result = None

    n_workers = min(max(1, (os.cpu_count() or 4)), 16)

    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futures = {}
        for _, direction in sorted_candidates:
            future = ex.submit(
                check_disassembly_path,
                part_name, part_shape, obstacle_shapes, direction,
                max_distance, 20, collision_data)
            futures[future] = direction

        for future in as_completed(futures):
            if feasible_found:
                future.cancel()
                continue

            direction = futures[future]
            try:
                result = future.result(timeout=300)
            except Exception:
                continue

            if result.get("feasible", False):
                feasible_found = True
                feasible_dir = direction
                feasible_result = result
                for f in futures:
                    f.cancel()
                break

            if result.get("max_safe_distance", -1) > best_safe:
                best_safe = result["max_safe_distance"]
                best_result = result
                best_dir = direction

    if feasible_found:
        return feasible_dir, feasible_result

    if best_dir is None:
        best_dir = preferred_dir
        best_result = {
            "feasible": False,
            "max_safe_distance": 0.0,
            "collision_at_step": None,
            "collision_with": None,
            "total_steps": 20,
            "reason": "no candidate succeeded",
        }

    return best_dir, best_result


def find_all_blockers(part_name, part_shape, obstacle_shapes,
                      preferred_dir, max_distance=500.0,
                      collision_data=None):
    """
    Search all 26 candidate directions and collect every blocking part.

    Unlike find_best_feasible_direction (which stops at the first feasible
    direction and cancels remaining tasks), this function completes all 26
    checks to gather the union of all blockers across all directions.

    This is essential for the dependency chain analyzer to recursively
    resolve every part that obstructs the target.

    Returns:
        dict: {
            "feasible": bool,              # any direction works
            "best_direction": [x,y,z],     # feasible dir if found, else
                                            # direction with largest safe_distance
            "best_result": {...},          # check result for best_direction
            "blockers": list[str],         # sorted unique blockers across ALL directions
            "per_direction": list[dict],   # per-direction details
        }
    """
    preferred = np.array(preferred_dir, dtype=np.float64)
    pnorm = np.linalg.norm(preferred)

    sorted_candidates = []
    for cand in CANDIDATE_DIRS:
        if pnorm > 1e-10:
            dot = float(np.dot(preferred / pnorm, cand))
        else:
            dot = 0.0
        sorted_candidates.append((dot, cand.tolist()))

    sorted_candidates.sort(key=lambda x: -x[0])

    blockers = set()
    per_direction = []
    feasible_dir = None
    feasible_result = None
    best_safe = -1.0
    best_dir = None
    best_result = None

    n_workers = min(max(1, (os.cpu_count() or 4)), 16)

    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futures = {}
        for _, direction in sorted_candidates:
            future = ex.submit(
                check_disassembly_path,
                part_name, part_shape, obstacle_shapes, direction,
                max_distance, 20, collision_data)
            futures[future] = direction

        for future in as_completed(futures):
            direction = futures[future]
            try:
                result = future.result(timeout=300)
            except Exception:
                continue

            cw = result.get("collision_with")
            if cw:
                if isinstance(cw, str) and cw.strip():
                    blockers.add(cw.strip())
                elif isinstance(cw, (list, tuple)):
                    for b in cw:
                        if b:
                            blockers.add(str(b).strip())

            per_direction.append({
                "direction": direction,
                "feasible": result.get("feasible", False),
                "safe_distance": result.get("max_safe_distance", 0.0),
                "collision_with": cw,
            })

            if result.get("feasible", False):
                if feasible_dir is None:
                    feasible_dir = direction
                    feasible_result = result

            safe = result.get("max_safe_distance", 0.0)
            if safe > best_safe:
                best_safe = safe
                best_result = result
                best_dir = direction

    if feasible_dir is not None:
        return {
            "feasible": True,
            "best_direction": feasible_dir,
            "best_result": feasible_result,
            "blockers": sorted(blockers),
            "per_direction": per_direction,
        }

    if best_dir is None:
        best_dir = preferred_dir
        best_result = {
            "feasible": False,
            "max_safe_distance": 0.0,
            "collision_at_step": None,
            "collision_with": None,
            "total_steps": 20,
            "reason": "no candidate succeeded",
        }

    return {
        "feasible": False,
        "best_direction": best_dir,
        "best_result": best_result,
        "blockers": sorted(blockers),
        "per_direction": per_direction,
    }


def _collect_leaf_descendants(sa_name, sub_assemblies, part_map, result_set,
                              memo=None):
    """Recursively collect all leaf part names under a sub-assembly."""
    if memo is not None:
        if sa_name in memo:
            if memo[sa_name] is not None:
                result_set.update(memo[sa_name])
            return
        memo[sa_name] = None

    sa_leaves = set()
    for sa in sub_assemblies:
        if sa["name"] == sa_name:
            for child in sa.get("child_names", []):
                if child in part_map:
                    sa_leaves.add(child)
                else:
                    child_set = set()
                    _collect_leaf_descendants(child, sub_assemblies,
                                              part_map, child_set, memo)
                    sa_leaves.update(child_set)
            break
    if memo is not None:
        memo[sa_name] = sa_leaves
    result_set.update(sa_leaves)


def filter_obstacles_by_compound_bbox(part_name, part_shape, remaining_names,
                                      part_map, sub_assemblies, collision_data,
                                      max_distance=500.0, sa_bbox_cache=None):
    """Filter obstacles using Compound-level Bnd_Box to exclude far-away groups.

    For each sub-assembly, merge AABBs of all descendant leaf parts into a
    compound-level bounding box. A compound whose AABB does not overlap the
    target part's expanded AABB can have all its leaf parts skipped,
    dramatically reducing the obstacle count for collision checking.

    Args:
        sa_bbox_cache: precomputed dict[sa_name -> (bmin, bmax)].
                       If provided, avoids rebuilding compound bboxes.
    """
    if not sub_assemblies or len(remaining_names) < 50:
        return [(n, part_map[n]["shape"]) for n in remaining_names if n != part_name]

    part_data = collision_data.get(part_name)
    if part_data is None or part_data.aabb_min is None:
        return [(n, part_map[n]["shape"]) for n in remaining_names if n != part_name]

    expanded_min = part_data.aabb_min - max_distance
    expanded_max = part_data.aabb_max + max_distance

    if sa_bbox_cache is not None:
        sa_leaves = sa_bbox_cache["_leaves"]
        sa_bbox = sa_bbox_cache["_bbox"]
        all_known = sa_bbox_cache["_all_known"]
    else:
        memo = {}
        sa_leaves = {}
        all_known = set()
        sa_bbox = {}

        for sa in sub_assemblies:
            sa_name = sa["name"]
            leaves = set()
            _collect_leaf_descendants(sa_name, sub_assemblies, part_map,
                                      leaves, memo)
            sa_leaves[sa_name] = leaves
            all_known.update(leaves)

            bmin = None
            bmax = None
            for leaf_name in leaves:
                cd = collision_data.get(leaf_name)
                if cd is not None and cd.aabb_min is not None:
                    if bmin is None:
                        bmin = cd.aabb_min.copy()
                        bmax = cd.aabb_max.copy()
                    else:
                        bmin = np.minimum(bmin, cd.aabb_min)
                        bmax = np.maximum(bmax, cd.aabb_max)
            if bmin is not None:
                sa_bbox[sa_name] = (bmin, bmax)

    if not sa_bbox:
        return [(n, part_map[n]["shape"]) for n in remaining_names if n != part_name]

    remaining_set = set(remaining_names)
    remaining_set.discard(part_name)

    filtered = set()
    for sa_name, (bmin, bmax) in sa_bbox.items():
        if _aabb_overlap_np(expanded_min, expanded_max, bmin, bmax):
            filtered.update(remaining_set & sa_leaves.get(sa_name, set()))

    for n in remaining_set - all_known:
        filtered.add(n)

    return [(n, part_map[n]["shape"]) for n in filtered]


def precompute_compound_bbox_cache(sub_assemblies, part_map, collision_data):
    """Precompute compound-level bounding boxes for fast obstacle filtering.

    Returns a dict to pass as sa_bbox_cache to filter_obstacles_by_compound_bbox,
    or None if sub_assemblies is empty.
    """
    if not sub_assemblies:
        return None

    memo = {}
    sa_leaves = {}
    all_known = set()
    sa_bbox = {}

    for sa in sub_assemblies:
        sa_name = sa["name"]
        leaves = set()
        _collect_leaf_descendants(sa_name, sub_assemblies, part_map,
                                  leaves, memo)
        sa_leaves[sa_name] = leaves
        all_known.update(leaves)

        bmin = None
        bmax = None
        for leaf_name in leaves:
            cd = collision_data.get(leaf_name)
            if cd is not None and cd.aabb_min is not None:
                if bmin is None:
                    bmin = cd.aabb_min.copy()
                    bmax = cd.aabb_max.copy()
                else:
                    bmin = np.minimum(bmin, cd.aabb_min)
                    bmax = np.maximum(bmax, cd.aabb_max)
        if bmin is not None:
            sa_bbox[sa_name] = (bmin, bmax)

    return {
        "_leaves": sa_leaves,
        "_bbox": sa_bbox,
        "_all_known": all_known,
    }


def check_obstacle_set(part_shape, obstacle_set, direction,
                       max_distance=500.0, steps=20):
    """Simple interference check: is there any obstacle in the path?"""
    others = [(str(i), s) for i, s in enumerate(obstacle_set)]
    result = check_disassembly_path("part", part_shape, others, direction,
                                    max_distance, steps)
    return result["feasible"], result["max_safe_distance"]
