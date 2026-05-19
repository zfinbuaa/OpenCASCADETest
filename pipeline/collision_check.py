"""
Swept collision detection along disassembly paths.

Optimized version using triangle-mesh-level collision detection with
AABB bounding-volume hierarchies and binary-search step refinement.

Includes direction search and AABB-level fast pre-filtering.
Falls back to the BRep boolean method when mesh data is unavailable.
"""

import sys
import numpy as np
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.gp import gp_Trsf, gp_Vec
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib


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
    """Fast triangle-triangle overlap test using separating axis theorem."""
    e0 = v1 - v0
    e1 = v2 - v0
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

    for edge_a in [e0, e1, v2 - v1]:
        for edge_b in [u1 - u0, u2 - u0, u2 - u1]:
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

    return True


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
    """Pre-compute mesh and AABB data for all parts."""
    data = {}
    n = len(parts)
    for idx, part in enumerate(parts):
        name = part["name"]
        if n > 10 and (idx % 10 == 0 or idx == n - 1):
            sys.stdout.write("\r  meshing for collision: {}/{}...".format(idx + 1, n))
            sys.stdout.flush()
        data[name] = MeshCollisionData(part["shape"], linear_deflection)
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
                           collision_data=None):
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

    Returns:
        dict with feasible, max_safe_distance, collision_at_step, collision_with, total_steps.
    """
    dir_np = np.array(direction, dtype=np.float64)

    if collision_data is not None:
        return _check_path_mesh(
            part_name, part_shape, other_shapes, dir_np,
            max_distance, steps, collision_data)

    return _check_path_brep(
        part_shape, other_shapes, dir_np,
        max_distance, steps)


def _check_path_mesh(part_name, part_shape, other_shapes, dir_np,
                     max_distance, steps, collision_data):
    """Mesh-based collision check with AABB pre-filter and binary search."""
    part_data = collision_data.get(part_name)

    if part_data is None or part_data.vertices is None or part_data.tree is None:
        return _check_path_brep(
            part_shape, other_shapes, dir_np.tolist(),
            max_distance, steps)

    obs_data_list = []
    for other_name, other_shape in other_shapes:
        od = collision_data.get(other_name)
        if od is None or od.vertices is None or od.tree is None:
            obs_data_list.append((other_name, None, None, None, None, None, other_shape))
        else:
            obs_data_list.append((other_name, od.vertices, od.triangles,
                                  od.tree, od.aabb_min, od.aabb_max, other_shape))

    coarse_steps = max(5, steps // 4)
    step_size = max_distance / coarse_steps

    collision_step = -1
    collision_name = None

    for step in range(1, coarse_steps + 1):
        dist = step * step_size
        offset = dir_np * dist

        moved_verts = part_data.vertices + offset
        moved_tree = _build_aabb_tree(moved_verts, part_data.triangles)
        moved_aabb_min, moved_aabb_max = _compute_aabb_np(moved_verts)

        for other_name, obs_v, obs_t, obs_tree, obs_amin, obs_amax, obs_shape in obs_data_list:
            if obs_tree is not None and obs_amin is not None:
                if _check_mesh_intersection(moved_verts, part_data.triangles,
                                            moved_tree, moved_aabb_min, moved_aabb_max,
                                            obs_v, obs_t, obs_tree,
                                            obs_amin, obs_amax):
                    collision_step = step
                    collision_name = other_name
                    break
            else:
                vec = gp_Vec(dir_np[0] * dist, dir_np[1] * dist, dir_np[2] * dist)
                trsf = gp_Trsf()
                trsf.SetTranslation(vec)
                moved_shape = BRepBuilderAPI_Transform(part_shape, trsf).Shape()
                if _has_interference_brep(moved_shape, obs_shape, part_data.volume):
                    collision_step = step
                    collision_name = other_name
                    break

        if collision_step > 0:
            break

    if collision_step < 0:
        return {
            "feasible": True,
            "max_safe_distance": max_distance,
            "collision_at_step": -1,
            "collision_with": None,
            "total_steps": steps,
        }

    lo = (collision_step - 1) * step_size
    hi = collision_step * step_size

    for _ in range(8):
        mid = (lo + hi) / 2.0
        offset = dir_np * mid
        moved_verts = part_data.vertices + offset
        moved_tree = _build_aabb_tree(moved_verts, part_data.triangles)
        moved_aabb_min, moved_aabb_max = _compute_aabb_np(moved_verts)

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
                vec = gp_Vec(dir_np[0] * mid, dir_np[1] * mid, dir_np[2] * mid)
                trsf = gp_Trsf()
                trsf.SetTranslation(vec)
                moved_shape = BRepBuilderAPI_Transform(part_shape, trsf).Shape()
                if _has_interference_brep(moved_shape, obs_shape, part_data.volume):
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
        "total_steps": steps,
    }


def _check_path_brep(part_shape, other_shapes, direction,
                     max_distance, steps):
    """Original BRep boolean collision check (fallback)."""
    step_size = max_distance / steps

    for step in range(1, steps + 1):
        dist = step * step_size
        vec = gp_Vec(direction[0] * dist,
                     direction[1] * dist,
                     direction[2] * dist)
        transform = gp_Trsf()
        transform.SetTranslation(vec)
        moved_shape = BRepBuilderAPI_Transform(part_shape, transform).Shape()

        vol_moved = _compute_volume(moved_shape)
        if vol_moved is None:
            continue

        for other_name, other_shape in other_shapes:
            if _has_interference_brep(moved_shape, other_shape, vol_moved):
                safe_dist = (step - 1) * step_size
                return {
                    "feasible": False,
                    "max_safe_distance": safe_dist,
                    "collision_at_step": step,
                    "collision_with": other_name,
                    "total_steps": steps,
                }

    return {
        "feasible": True,
        "max_safe_distance": max_distance,
        "collision_at_step": -1,
        "collision_with": None,
        "total_steps": steps,
    }


def find_best_feasible_direction(part_name, part_shape, obstacle_shapes,
                                  preferred_dir, max_distance=500.0,
                                  collision_data=None):
    """
    Search for a feasible disassembly direction for a part.

    Tries directions in priority order:
    1. preferred_dir
    2. 26 candidate directions sorted by dot(preferred, candidate)

    Returns:
        tuple: (best_direction, check_result)
            best_direction: [x, y, z]
            check_result: dict from check_disassembly_path
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

    for _, direction in sorted_candidates:
        result = check_disassembly_path(
            part_name, part_shape, obstacle_shapes, direction,
            max_distance, steps=20, collision_data=collision_data)

        if result["feasible"]:
            return direction, result

        if result["max_safe_distance"] > best_safe:
            best_safe = result["max_safe_distance"]
            best_result = result
            best_dir = direction

    if best_dir is None:
        best_dir = preferred_dir
        best_result = {
            "feasible": False,
            "max_safe_distance": 0.0,
            "collision_at_step": 1,
            "collision_with": None,
            "total_steps": 20,
        }

    return best_dir, best_result


def check_obstacle_set(part_shape, obstacle_set, direction,
                       max_distance=500.0, steps=20):
    """Simple interference check: is there any obstacle in the path?"""
    others = [(str(i), s) for i, s in enumerate(obstacle_set)]
    result = check_disassembly_path("part", part_shape, others, direction,
                                    max_distance, steps)
    return result["feasible"], result["max_safe_distance"]
