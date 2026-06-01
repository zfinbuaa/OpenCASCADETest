"""
Dependency chain analyzer for disassembly planning.

Given a target part within a full assembly, computes the ordered sequence
of parts that must be removed before the target part can be removed.

Algorithm:
  1. For target part T, try to remove it against all other parts as obstacles.
  2. Record which parts cause collisions → blockers(T).
  3. For each blocker B, recursively compute dependency_chain(B).
  4. Combine results: topological sort → assign stages (inner stages first).
  5. Handle cycles via deadlock resolution (force-remove deepest blocker).
"""

import logging
import sys
import numpy as np

logger = logging.getLogger(__name__)


def _precompute_centroids(part_map):
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.BRepGProp import brepgprop
    out = {}
    for name, p in part_map.items():
        try:
            props = GProp_GProps()
            brepgprop.VolumeProperties(p["shape"], props)
            c = props.CentreOfMass()
            out[name] = np.array([c.X(), c.Y(), c.Z()])
        except Exception:
            out[name] = None
    return out


def compute_dependency_chain(parts, directions, collision_data,
                              target_name, max_distance=500.0,
                              assembly_centroid=None, sub_assemblies=None,
                              max_recursion=50):
    """
    Compute the disassembly dependency chain for a target part.

    Args:
        parts: list of part dicts with 'name', 'shape'.
        directions: dict of name -> [x,y,z] disassembly directions.
        collision_data: dict from prepare_collision_data().
        target_name: name of the target part to disassemble.
        max_distance: movement distance for collision check (mm).
        assembly_centroid: ndarray(3) assembly center (unused, forward compat).
        sub_assemblies: list of sub-assembly dicts (unused, forward compat).
        max_recursion: max recursion depth to prevent infinite loops.

    Returns:
        tuple: (stages, verified_directions, distance_multipliers, details)
            stages: list of stage lists (innermost first, target last).
            verified_directions: dict of name -> verified direction.
            distance_multipliers: dict of name -> stage-based multiplier.
            details: list of result dicts for each part in the chain.
    """
    from pipeline.collision_check import check_disassembly_path
    from pipeline.path_searcher import find_feasible_direction

    part_map = {p["name"]: p for p in parts}

    if target_name not in part_map:
        logger.error("target part '%s' not found", target_name)
        return [], {}, {}, []

    verified_dirs = dict(directions)
    chain_order = []
    chain_set = set()
    details = []

    centroids = _precompute_centroids(part_map)

    _resolve_chain(target_name, part_map, set(part_map.keys()),
                   verified_dirs, collision_data, max_distance,
                   chain_order, chain_set, details, 0, max_recursion,
                   centroids)

    stages = [[name] for name in chain_order]
    distance_multipliers = {}
    for idx, name in enumerate(chain_order):
        distance_multipliers[name] = idx + 1

    return stages, verified_dirs, distance_multipliers, details


def _resolve_chain(target_name, part_map, all_part_names,
                   verified_dirs, collision_data, max_distance,
                   chain_order, chain_set, details, depth, max_depth,
                   centroids):
    """
    Recursively resolve the dependency chain for a target part.

    Finds blockers → resolves each blocker first → then adds target.
    """
    if target_name in chain_set:
        return
    if depth > max_depth:
        logger.warning("dependency chain max depth %d reached for %s", max_depth, target_name)
        return

    part = part_map[target_name]
    preferred_dir = verified_dirs.get(target_name, [0, 1, 0])

    obstacles = [(n, part_map[n]["shape"])
                 for n in all_part_names if n != target_name and n not in chain_set]

    from pipeline.collision_check import find_best_feasible_direction

    result = find_best_feasible_direction(
        target_name, part["shape"], obstacles, preferred_dir,
        max_distance, collision_data)

    verified_dirs[target_name] = result[0]

    if result[1]["feasible"]:
        chain_order.append(target_name)
        chain_set.add(target_name)
        details.append({
            "part": target_name, "stage": len(chain_order),
            "feasible": True,
            "direction": result[0],
            "safe_distance": result[1]["max_safe_distance"],
        })
        return

    collision_with = result[1].get("collision_with", None)
    blocker_names = []
    if collision_with and isinstance(collision_with, str) and collision_with.strip():
        blocker_names = [collision_with.strip()]
    elif collision_with and isinstance(collision_with, list):
        blocker_names = [str(b).strip() for b in collision_with if b]

    if not blocker_names:
        chain_order.append(target_name)
        chain_set.add(target_name)
        details.append({
            "part": target_name, "stage": len(chain_order),
            "feasible": False,
            "direction": result[0],
            "safe_distance": result[1]["max_safe_distance"],
            "collision_with": collision_with,
            "note": "no blockers identified, force-removed",
        })
        return

    valid_blockers = [b for b in blocker_names
                      if b in part_map and b not in chain_set and b != target_name]

    if not valid_blockers:
        chain_order.append(target_name)
        chain_set.add(target_name)
        details.append({
            "part": target_name, "stage": len(chain_order),
            "feasible": False,
            "direction": result[0],
            "safe_distance": result[1]["max_safe_distance"],
            "collision_with": collision_with,
            "note": "all blockers already resolved, force-removed",
        })
        return

    target_centroid = centroids.get(target_name, np.array([0.0, 0.0, 0.0]))
    def _distance_to_target(blocker_name):
        bc = centroids.get(blocker_name, np.array([0.0, 0.0, 0.0]))
        return float(np.linalg.norm(bc - target_centroid))

    valid_blockers.sort(key=_distance_to_target, reverse=True)

    for blocker in valid_blockers:
        _resolve_chain(blocker, part_map, all_part_names,
                       verified_dirs, collision_data, max_distance,
                       chain_order, chain_set, details, depth + 1, max_depth,
                       centroids)

    if target_name not in chain_set:
        from pipeline.collision_check import check_disassembly_path
        recheck_obstacles = [(n, part_map[n]["shape"])
                             for n in all_part_names
                             if n != target_name and n not in chain_set]
        recheck = check_disassembly_path(
            target_name, part["shape"], recheck_obstacles,
            verified_dirs.get(target_name, result[0]),
            max_distance, collision_data)
        chain_order.append(target_name)
        chain_set.add(target_name)
        details.append({
            "part": target_name, "stage": len(chain_order),
            "feasible": recheck["feasible"],
            "direction": verified_dirs.get(target_name, result[0]),
            "safe_distance": recheck["max_safe_distance"],
            "collision_with": recheck.get("collision_with", collision_with),
        })
