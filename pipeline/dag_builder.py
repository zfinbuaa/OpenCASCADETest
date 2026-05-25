"""
Disassembly DAG builder — collision-driven version.

Generates disassembly stages by actually checking whether each part can
be removed along a direction without colliding with remaining parts.
Directions are verified and can be corrected during DAG construction.

Algorithm:
  1. Stage 1: Fasteners (try initial direction, search if blocked)
  2. Stage 2+: Greedy BFS from outside-in (parts farthest from
     assembly center first). For each part:
     - Try initial direction with collision check
     - If blocked, search 26 candidate directions
     - If feasible → add to current stage, record verified direction
     - If all directions blocked → defer to next stage
  3. Deadlock: force-remove part with largest safe_distance
  4. distanceMultiplier = stage_number (inner parts explode farther)
"""

import sys
import numpy as np
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop


def _compute_centroid(shape):
    """Compute centroid of a TopoDS_Shape."""
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


def build_disassembly_dag_v2(parts, directions, collision_data,
                              fasteners, max_distance=500.0,
                              assembly_centroid=None, sub_assemblies=None):
    """
    Collision-driven disassembly plan generation — item-by-item removal.

    Parts are removed one at a time (outermost first). Once a part is
    confirmed removable, it is immediately excluded from the obstacle set
    for subsequent parts, avoiding false-positive collisions within the
    same stage.

    Uses Compound-level Bnd_Box filtering to skip far-away obstacles.

    Args:
        parts: list of part dicts with 'name', 'shape', 'parent'.
        directions: dict of name -> [x,y,z] initial direction guesses.
        collision_data: dict from prepare_collision_data().
        fasteners: list of fastener part names.
        max_distance: movement distance for collision check (mm).
        assembly_centroid: ndarray(3) assembly center point.
        sub_assemblies: list of sub-assembly dicts from flatten_assembly_tree.

    Returns:
        tuple: (stages, verified_directions, distance_multipliers, details)
    """
    from pipeline.collision_check import (
        check_disassembly_path, find_best_feasible_direction,
        filter_obstacles_by_compound_bbox
    )

    part_map = {p["name"]: p for p in parts}
    part_names = list(part_map.keys())

    if assembly_centroid is None:
        centroids = {p["name"]: _compute_centroid(p["shape"]) for p in parts}
        assembly_centroid = np.mean(list(centroids.values()), axis=0)
    else:
        centroids = {p["name"]: _compute_centroid(p["shape"]) for p in parts}

    distances_to_center = {}
    for name in part_names:
        diff = centroids[name] - assembly_centroid
        distances_to_center[name] = float(np.linalg.norm(diff))

    verified_dirs = dict(directions)
    remaining = set(part_names)
    stages = []
    details = []
    distance_multipliers = {}

    # ── Stage 1: Fasteners ──────────────────────────────────
    fastener_set = set(fasteners) & remaining
    if fastener_set:
        sys.stdout.write("  Stage 1: checking {} fasteners...\n".format(
            len(fastener_set)))
        sys.stdout.flush()

        stage1 = []
        for name in sorted(fastener_set):
            part = part_map[name]
            obstacles = [(n, part_map[n]["shape"])
                         for n in remaining if n != name]

            result = check_disassembly_path(
                name, part["shape"], obstacles, verified_dirs[name],
                max_distance, collision_data=collision_data)

            if result["feasible"]:
                stage1.append(name)
                details.append({
                    "part": name, "stage": 1, "feasible": True,
                    "direction": verified_dirs[name],
                    "safe_distance": result["max_safe_distance"],
                })
            else:
                best_dir, best_result = find_best_feasible_direction(
                    name, part["shape"], obstacles, verified_dirs[name],
                    max_distance, collision_data)

                verified_dirs[name] = best_dir
                stage1.append(name)
                details.append({
                    "part": name, "stage": 1,
                    "feasible": best_result["feasible"],
                    "direction": best_dir,
                    "safe_distance": best_result["max_safe_distance"],
                    "collision_with": best_result.get("collision_with"),
                })

            distance_multipliers[name] = 1

        stages.append(stage1)
        remaining -= set(stage1)

        sys.stdout.write("    {} fasteners placed in stage 1\n".format(
            len(stage1)))
        sys.stdout.flush()

    # ── Stage 2+: Item-by-item removal (outermost first) ──
    stage_num = 2
    max_stages = len(part_names)

    while remaining and stage_num <= max_stages:
        sorted_remaining = sorted(remaining,
                                  key=lambda n: distances_to_center.get(n, 0),
                                  reverse=True)

        sys.stdout.write("  Stage {}: checking {} remaining parts "
                         "(item-by-item)...\n".format(
                             stage_num, len(sorted_remaining)))
        sys.stdout.flush()

        current_stage = []
        stage_details = []
        deferred = []
        best_deferred = None
        best_deferred_safe = -1.0
        best_deferred_dir = None

        checked = 0
        for name in sorted_remaining:
            if name not in remaining:
                continue
            part = part_map[name]

            obstacles = filter_obstacles_by_compound_bbox(
                name, part["shape"], list(remaining),
                part_map, sub_assemblies, collision_data,
                max_distance)

            result = check_disassembly_path(
                name, part["shape"], obstacles, verified_dirs[name],
                max_distance, collision_data=collision_data)

            checked += 1

            if result["feasible"]:
                current_stage.append(name)
                remaining.discard(name)
                stage_details.append({
                    "part": name, "stage": stage_num, "feasible": True,
                    "direction": verified_dirs[name],
                    "safe_distance": result["max_safe_distance"],
                })
            else:
                best_dir, best_result = find_best_feasible_direction(
                    name, part["shape"], obstacles, verified_dirs[name],
                    max_distance, collision_data)

                if best_result["feasible"]:
                    verified_dirs[name] = best_dir
                    current_stage.append(name)
                    remaining.discard(name)
                    stage_details.append({
                        "part": name, "stage": stage_num, "feasible": True,
                        "direction": best_dir,
                        "safe_distance": best_result["max_safe_distance"],
                    })
                else:
                    deferred.append(name)
                    if best_result["max_safe_distance"] > best_deferred_safe:
                        best_deferred_safe = best_result["max_safe_distance"]
                        best_deferred = name
                        best_deferred_dir = best_dir
                    stage_details.append({
                        "part": name, "stage": stage_num, "feasible": False,
                        "direction": best_dir,
                        "safe_distance": best_result["max_safe_distance"],
                        "collision_with": best_result.get("collision_with"),
                    })

            if checked % 10 == 0:
                sys.stdout.write(
                    "\r    checked {}/{} ({} feasible, {} deferred)".format(
                        checked, len(sorted_remaining),
                        len(current_stage), len(deferred)))
                sys.stdout.flush()

        # ── Deadlock resolution: force-remove the best-deferred part ──
        if not current_stage and best_deferred is not None:
            if best_deferred in remaining:
                verified_dirs[best_deferred] = best_deferred_dir
                current_stage.append(best_deferred)
                remaining.discard(best_deferred)
                for d in stage_details:
                    if d["part"] == best_deferred:
                        d["feasible"] = False
                        d["direction"] = best_deferred_dir
                        d["note"] = "force-removed"
                        break

        if current_stage:
            for name in current_stage:
                distance_multipliers[name] = stage_num
            stages.append(current_stage)
            details.extend(stage_details)

            sys.stdout.write(
                "\r    stage {} done: {} parts removed, {} remaining  \n".format(
                    stage_num, len(current_stage), len(remaining)))
            sys.stdout.flush()
        else:
            sys.stdout.write(
                "    WARNING: no parts could be removed at stage {}\n".format(
                    stage_num))
            sys.stdout.flush()
            break

        stage_num += 1

    return stages, verified_dirs, distance_multipliers, details


def build_disassembly_dag(parts, contacts, fasteners=None, directions=None):
    """
    Legacy DAG builder (contact-graph based, no collision verification).

    Kept for --validate mode compatibility.
    """
    if fasteners is None:
        fasteners = []

    part_names = [p["name"] for p in parts]
    fastener_set = set(fasteners)

    blocked_by = {name: set() for name in part_names}
    for c in contacts:
        a, b = c["partA"], c["partB"]
        blocked_by[a].add(b)
        blocked_by[b].add(a)

    for name in part_names:
        if name not in blocked_by:
            blocked_by[name] = set()

    removed = set()
    stages = []

    stage1 = []
    for name in fastener_set:
        if name in part_names:
            stage1.append(name)

    if stage1:
        stages.append(stage1)
        removed.update(stage1)

    remaining = set(part_names) - removed

    while remaining:
        current_stage = []
        for name in sorted(remaining):
            effective_blockers = blocked_by[name] & remaining
            if len(effective_blockers) == 0:
                current_stage.append(name)

        if not current_stage:
            min_name = min(remaining,
                           key=lambda n: len(blocked_by[n] & remaining))
            current_stage = [min_name]

        stages.append(current_stage)
        removed.update(current_stage)
        remaining = set(part_names) - removed

    return stages


def assign_stages_to_parts(parts, stages):
    """Assign a stage number to each part based on the disassembly stages."""
    stage_map = {}
    for idx, stage_parts in enumerate(stages):
        for name in stage_parts:
            stage_map[name] = idx + 1
    return stage_map
