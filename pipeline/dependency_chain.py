"""
Dependency chain analyzer for disassembly planning.

Given a target part within a full assembly, computes the ordered sequence
of parts that must be removed before the target part can be removed,
choosing the disassembly DIRECTION that requires the FEWEST total removals.

Algorithm (direction-optimizing):
  1. For target part T, call find_all_blockers across all 26 directions
     to discover all candidate directions and their blocker sets.
  2. Pick the BEST direction by simulating the full recursive cost
     (= total parts to remove if we follow that direction).
  3. Only recurse on the blockers of the chosen direction, not the union.
  4. Top-K beam search (K=4) and best-so-far pruning to control cost.
  5. Tie-breaker: shallower chain depth wins over equal cost.
  6. Handle deadlock via force-removal (record residual blockers).
"""

import logging
import sys
import numpy as np

logger = logging.getLogger(__name__)


_BEAM_K = 4
_INF = 10 ** 9


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


def _resolve_target_node(target_name, part_map, sub_assemblies):
    """
    Resolve a user-provided target name into an actual part in part_map.

    Handles three cases:
      1. target_name is already a leaf part → return as-is.
      2. target_name is a sub-assembly node → return the first leaf descendant
         plus the full set of descendants (treated as a unit).
      3. target_name partially matches some part name → fuzzy match by suffix.

    Returns:
        tuple: (resolved_name, descendants_set_or_None, merged_shape_or_None)
    """
    if target_name in part_map:
        return target_name, None, None

    if sub_assemblies:
        from pipeline.collision_check import _collect_leaf_descendants
        for sa in sub_assemblies:
            if sa.get("name") == target_name:
                leaves = set()
                _collect_leaf_descendants(target_name, sub_assemblies,
                                          part_map, leaves)
                valid = [n for n in leaves if n in part_map]
                if valid:
                    merged_shape = _merge_part_shapes(part_map, valid)
                    return valid[0], set(valid), merged_shape

    suffix_matches = [n for n in part_map
                      if n.endswith(target_name) or target_name in n]
    if suffix_matches:
        suffix_matches.sort(key=len)
        return suffix_matches[0], None, None

    return target_name, None, None


def _merge_part_shapes(part_map, part_names):
    """
    Merge multiple leaf part shapes into a single compound for collision check.

    Uses TopoDS_Builder to create a Compound containing all leaf shapes
    with their world-space transforms applied. This allows the dependency
    chain analyzer to treat a sub-assembly as a single rigid body.

    Args:
        part_map: dict of name -> part dict with 'shape' and optional 'transform'.
        part_names: list of part names to merge.

    Returns:
        TopoDS_Compound or None if no valid shapes found.
    """
    from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Builder
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCC.Core.gp import gp_Trsf

    builder = TopoDS_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    added = 0

    for name in part_names:
        p = part_map.get(name)
        if p is None or p.get("shape") is None:
            continue
        shape = p["shape"]
        transform = p.get("transform")
        if transform and len(transform) == 16:
            try:
                import numpy as np
                mat = np.array(transform, dtype=np.float64).reshape(4, 4, order='F')
                trsf = gp_Trsf()
                trsf.SetValues(
                    float(mat[0][0]), float(mat[1][0]), float(mat[2][0]), float(mat[3][0]),
                    float(mat[0][1]), float(mat[1][1]), float(mat[2][1]), float(mat[3][1]),
                    float(mat[0][2]), float(mat[1][2]), float(mat[2][2]), float(mat[3][2]),
                )
                shape = BRepBuilderAPI_Transform(shape, trsf, True).Shape()
            except Exception:
                pass
        builder.Add(compound, shape)
        added += 1

    return compound if added > 0 else None


def _simulate_dir_blockers(target_name, part_map, all_part_names,
                            chain_set, skip_set, direction, collision_data,
                            max_distance, sub_assemblies=None,
                            sa_bbox_cache=None,
                            target_shape_override=None,
                            interference_tolerance=0.0):
    """
    Probe one direction and return ALL parts blocking the path.

    Uses check_disassembly_path with report_all_collisions=True to gather
    every obstacle that intersects the target's swept volume along the
    given direction.

    Args:
        target_shape_override: if provided, use this shape for collision check
            instead of part_map[target_name]["shape"].
        sub_assemblies: assembly hierarchy for spatial filtering.
        sa_bbox_cache: precomputed compound bbox cache for spatial filtering.

    Returns:
        tuple: (feasible: bool, blockers: list[str], safe_distance: float)
    """
    from pipeline.collision_check import check_disassembly_path

    target_shape = target_shape_override if target_shape_override is not None else part_map[target_name]["shape"]

    avail_names = [n for n in all_part_names
                   if n != target_name
                   and n not in chain_set
                   and n not in skip_set]

    if sub_assemblies:
        from pipeline.collision_check import filter_obstacles_by_compound_bbox
        obstacles = filter_obstacles_by_compound_bbox(
            target_name, target_shape, avail_names, part_map,
            sub_assemblies, collision_data, max_distance,
            sa_bbox_cache=sa_bbox_cache)
    else:
        obstacles = [(n, part_map[n]["shape"]) for n in avail_names]

    result = check_disassembly_path(
        target_name, target_shape, obstacles, direction,
        max_distance, collision_data=collision_data,
        report_all_collisions=True,
        interference_tolerance=interference_tolerance)

    blockers = result.get("collision_names", [])
    if not blockers and result.get("collision_with"):
        cw = result["collision_with"]
        if isinstance(cw, str):
            blockers = [cw]
        elif isinstance(cw, (list, tuple)):
            blockers = list(cw)

    blockers = [b for b in blockers
                if b in part_map
                and b not in chain_set
                and b not in skip_set
                and b != target_name]

    return (result.get("feasible", False), blockers,
            float(result.get("max_safe_distance", 0.0)))


def _find_static_blockers(target_name, part_map, avail_names,
                           target_volume=None, min_ratio=0.05):
    """Find parts that statically block the target via volume interference.

    Detects insert-type interference (bolt in hole, connector in socket) where
    a part overlaps significantly with the target at rest. Returns parts that
    must be removed before the target can move, regardless of direction.

    Args:
        target_name: name of the target part.
        part_map: dict of name -> part dict with 'shape'.
        avail_names: list of candidate obstacle part names to check.
        target_volume: precomputed volume of target (optional).
        min_ratio: interference_volume / min(vol_a, vol_b) threshold.

    Returns:
        list[str]: names of parts that statically block the target.
    """
    from pipeline.collision_check import has_static_insert_interference

    target_shape = part_map[target_name]["shape"]
    if target_shape is None:
        return []

    blockers = []
    for name in avail_names:
        p = part_map.get(name)
        if p is None or p.get("shape") is None:
            continue
        try:
            blocking, _ratio = has_static_insert_interference(
                target_shape, p["shape"],
                target_volume=target_volume,
                min_ratio=min_ratio)
        except Exception:
            continue
        if blocking:
            blockers.append(name)

    blockers.sort(key=lambda n: len(n))
    return blockers


def _find_optimal_direction(target_name, part_map, all_part_names,
                             verified_dirs, collision_data, max_distance,
                             chain_set, skip_set, depth, max_depth,
                             centroids, sim_cache, best_so_far=None,
                             sub_assemblies=None, sa_bbox_cache=None,
                             target_shape_override=None,
                             interference_tolerance=0.0,
                             _in_progress=None):
    """
    Find the optimal disassembly direction for `target_name`.

    Returns the direction that, after recursive resolution, requires the
    FEWEST total parts to be removed (counting target itself).

    Args:
        sim_cache: dict to memoize (part_name, frozenset(available_obstacles)) results
        best_so_far: int upper bound (cost from parent) for pruning
        sub_assemblies: assembly hierarchy for spatial obstacle filtering.
        sa_bbox_cache: precomputed compound bbox cache for spatial filtering.
        target_shape_override: if provided, use this shape for collision checks
            instead of part_map[target_name]["shape"] (for sub-assembly targets).
        _in_progress: internal set to detect cyclic dependencies.

    Returns:
        dict: {
            "feasible": bool,
            "direction": [x,y,z],
            "blockers": list[str],
            "cost": int,
            "chain_depth": int,
            "safe_distance": float,
            "considered": list[dict],
        }
    """
    from pipeline.collision_check import find_all_blockers, filter_obstacles_by_compound_bbox

    if _in_progress is None:
        _in_progress = set()

    if target_name in _in_progress:
        return {
            "feasible": False, "direction": [0, 1, 0], "blockers": [],
            "cost": _INF, "chain_depth": 1, "safe_distance": 0.0, "considered": [],
            "deadlock": True, "note": "cyclic dependency",
        }

    if depth > max_depth:
        return {
            "feasible": False, "direction": [0, 1, 0], "blockers": [],
            "cost": 1, "chain_depth": 1, "safe_distance": 0.0, "considered": [],
            "deadlock": True,
        }

    avail_key = frozenset(n for n in all_part_names
                          if n != target_name
                          and n not in chain_set
                          and n not in skip_set)
    cache_key = (target_name, avail_key)
    if cache_key in sim_cache:
        return sim_cache[cache_key]

    if target_name not in part_map:
        result = {
            "feasible": False, "direction": [0, 1, 0], "blockers": [],
            "cost": 0, "chain_depth": 0, "safe_distance": 0.0, "considered": [],
        }
        sim_cache[cache_key] = result
        return result

    _in_progress.add(target_name)

    target_shape = target_shape_override if target_shape_override is not None else part_map[target_name]["shape"]
    preferred_dir = verified_dirs.get(target_name, [0, 1, 0])

    avail_names = list(avail_key)

    # ── Level 1: Static insert-interference detection ──
    static_blockers = _find_static_blockers(
        target_name, part_map, avail_names, min_ratio=0.05)
    static_blocker_set = set(static_blockers)
    remaining_names = [n for n in avail_names if n not in static_blocker_set]

    if sub_assemblies:
        obstacles = filter_obstacles_by_compound_bbox(
            target_name, target_shape, remaining_names, part_map,
            sub_assemblies, collision_data, max_distance,
            sa_bbox_cache=sa_bbox_cache)
    else:
        obstacles = [(n, part_map[n]["shape"]) for n in remaining_names]

    # ── Level 2: Direction-based path search ──
    sweep = find_all_blockers(
        target_name, target_shape, obstacles, preferred_dir,
        max_distance, collision_data, max_directions=8,
        interference_tolerance=interference_tolerance)

    feasible_dirs = []
    blocked_dirs = []
    for pd in sweep.get("per_direction", []):
        if pd.get("feasible"):
            feasible_dirs.append(pd)
        else:
            blocked_dirs.append(pd)

    considered_summary = []

    if feasible_dirs:
        feasible_dirs.sort(key=lambda d: -d.get("safe_distance", 0))
        chosen = feasible_dirs[0]
        for fd in feasible_dirs[:_BEAM_K]:
            considered_summary.append({
                "direction": fd["direction"],
                "blockers_count": len(static_blockers),
                "chain_cost": 1 + len(static_blockers),
                "selected": fd is chosen,
            })
        result = {
            "feasible": True,
            "direction": chosen["direction"],
            "blockers": static_blockers,
            "cost": 1 + len(static_blockers),
            "chain_depth": 1 + len(static_blockers),
            "safe_distance": float(chosen.get("safe_distance", 0)),
            "considered": considered_summary,
            "static_blockers": static_blockers,
        }
        if static_blockers:
            result["note"] = "direction feasible but {} static blocker(s) must be removed first: {}".format(
                len(static_blockers), static_blockers[:5])
        sim_cache[cache_key] = result
        _in_progress.discard(target_name)
        return result

    candidates = []
    for bd in blocked_dirs:
        cw = bd.get("collision_with")
        initial_blockers = []
        if cw:
            if isinstance(cw, str):
                initial_blockers = [cw]
            elif isinstance(cw, (list, tuple)):
                initial_blockers = list(cw)
        initial_blockers = [b for b in initial_blockers
                            if b in part_map
                            and b not in chain_set
                            and b not in skip_set
                            and b != target_name]
        candidates.append({
            "direction": bd["direction"],
            "initial_blockers": initial_blockers,
            "initial_count": len(initial_blockers),
            "safe_distance": float(bd.get("safe_distance", 0)),
        })

    candidates.sort(key=lambda c: (c["initial_count"], -c["safe_distance"]))
    candidates = candidates[:_BEAM_K]

    if not candidates:
        result = {
            "feasible": False, "direction": preferred_dir,
            "blockers": static_blockers,
            "cost": 1 + len(static_blockers), "chain_depth": 1 + len(static_blockers),
            "safe_distance": 0.0, "considered": [], "deadlock": not static_blockers,
            "static_blockers": static_blockers,
        }
        if static_blockers:
            result["note"] = "no feasible direction, but {} static blocker(s) identified: {}".format(
                len(static_blockers), static_blockers[:5])
        sim_cache[cache_key] = result
        _in_progress.discard(target_name)
        return result

    best = None

    for cand in candidates:
        true_feasible, true_blockers, true_safe = _simulate_dir_blockers(
            target_name, part_map, all_part_names,
            chain_set, skip_set, cand["direction"], collision_data,
            max_distance, sub_assemblies=sub_assemblies,
            sa_bbox_cache=sa_bbox_cache,
            target_shape_override=target_shape_override,
            interference_tolerance=interference_tolerance)

        if true_feasible:
            cand_result = {
                "direction": cand["direction"],
                "blockers": [],
                "cost": 1,
                "chain_depth": 1,
                "safe_distance": true_safe,
            }
            considered_summary.append({
                "direction": cand["direction"],
                "blockers_count": 0,
                "chain_cost": 1,
                "selected": False,
            })
            if best is None or _better(cand_result, best):
                best = cand_result
            continue

        target_centroid = centroids.get(target_name, np.array([0.0, 0.0, 0.0]))
        def _dist_to_target(name):
            bc = centroids.get(name)
            if bc is None:
                return 0.0
            return float(np.linalg.norm(bc - target_centroid))
        true_blockers.sort(key=_dist_to_target, reverse=True)

        sim_chain_set = set(chain_set)
        cumulative_cost = 1
        max_sub_depth = 0
        pruned = False
        budget = best_so_far if best_so_far is not None else _INF
        if best is not None:
            budget = min(budget, best["cost"])

        for blocker in true_blockers:
            if blocker in sim_chain_set:
                continue
            if cumulative_cost >= budget:
                pruned = True
                break

            sub = _find_optimal_direction(
                blocker, part_map, all_part_names,
                verified_dirs, collision_data, max_distance,
                sim_chain_set, skip_set, depth + 1, max_depth,
                centroids, sim_cache,
                best_so_far=budget - cumulative_cost,
                sub_assemblies=sub_assemblies,
                sa_bbox_cache=sa_bbox_cache,
                interference_tolerance=interference_tolerance,
                _in_progress=_in_progress)

            cumulative_cost += sub["cost"]
            max_sub_depth = max(max_sub_depth, sub["chain_depth"])

            sim_chain_set.add(blocker)

        considered_summary.append({
            "direction": cand["direction"],
            "blockers_count": len(true_blockers),
            "chain_cost": cumulative_cost if not pruned else None,
            "selected": False,
            "pruned": pruned,
        })

        if pruned:
            continue

        cand_result = {
            "direction": cand["direction"],
            "blockers": true_blockers,
            "cost": cumulative_cost,
            "chain_depth": 1 + max_sub_depth,
            "safe_distance": true_safe,
        }
        if best is None or _better(cand_result, best):
            best = cand_result

    if best is None:
        cheapest = candidates[0]
        best = {
            "direction": cheapest["direction"],
            "blockers": cheapest["initial_blockers"],
            "cost": 1 + len(cheapest["initial_blockers"]),
            "chain_depth": 2,
            "safe_distance": cheapest["safe_distance"],
        }

    for cs in considered_summary:
        if cs["direction"] == best["direction"]:
            cs["selected"] = True
            break

    result = {
        "feasible": False,
        "direction": best["direction"],
        "blockers": static_blockers + best["blockers"],
        "cost": best["cost"] + len(static_blockers),
        "chain_depth": best["chain_depth"] + len(static_blockers),
        "safe_distance": best["safe_distance"],
        "considered": considered_summary,
        "static_blockers": static_blockers,
    }
    sim_cache[cache_key] = result
    _in_progress.discard(target_name)
    return result


def _better(a, b):
    """Return True if disassembly plan a is strictly better than b.
    Primary: lower cost. Tie-breaker: shallower chain_depth."""
    if a["cost"] < b["cost"]:
        return True
    if a["cost"] > b["cost"]:
        return False
    return a["chain_depth"] < b["chain_depth"]


def compute_dependency_chain(parts, directions, collision_data,
                              target_name, max_distance=100.0,
                              assembly_centroid=None, sub_assemblies=None,
                              max_recursion=50, optimize_direction=True,
                              interference_tolerance=0.0):
    """
    Compute the disassembly dependency chain for a target part.

    Args:
        parts: list of part dicts with 'name', 'shape'.
        directions: dict of name -> [x,y,z] initial direction guesses.
        collision_data: dict from prepare_collision_data().
        target_name: name of the target part (leaf or sub-assembly).
        max_distance: movement distance for collision check (mm).
        assembly_centroid: ndarray(3) assembly center (unused, forward compat).
        sub_assemblies: list of sub-assembly dicts (resolves sub-assembly target).
        max_recursion: max recursion depth.
        optimize_direction: if True, choose direction with fewest total removals.
        interference_tolerance: ignore penetrations up to this distance (mm).

    Returns:
        tuple: (stages, verified_directions, distance_multipliers, details)
    """
    part_map = {p["name"]: p for p in parts}

    resolved_target, target_descendants, merged_shape = _resolve_target_node(
        target_name, part_map, sub_assemblies)

    if resolved_target not in part_map:
        logger.error("target part '%s' not found (resolved to '%s')",
                     target_name, resolved_target)
        return [], {}, {}, [{
            "part": target_name,
            "stage": 0,
            "feasible": False,
            "note": "target not found in part_map",
        }]

    if target_descendants and len(target_descendants) > 1:
        sys.stdout.write("  Target '{}' resolved to sub-assembly with {} leaves\n".format(
            target_name, len(target_descendants)))
        sys.stdout.flush()

    target_shape = merged_shape if merged_shape is not None else part_map[resolved_target]["shape"]

    verified_dirs = dict(directions)
    chain_order = []
    chain_set = set()
    details = []

    centroids = _precompute_centroids(part_map)

    skip_for_target = set(target_descendants) if target_descendants else set()
    node_is_sub_assembly = (target_descendants is not None and len(target_descendants) > 1)

    sa_bbox_cache = None
    if sub_assemblies:
        from pipeline.collision_check import precompute_compound_bbox_cache
        try:
            sa_bbox_cache = precompute_compound_bbox_cache(
                sub_assemblies, part_map, collision_data)
        except Exception:
            pass

    _resolve_chain(resolved_target, part_map, set(part_map.keys()),
                   verified_dirs, collision_data, max_distance,
                   chain_order, chain_set, details, 0, max_recursion,
                   centroids, skip_for_target, optimize_direction,
                   sub_assemblies=sub_assemblies,
                   sa_bbox_cache=sa_bbox_cache,
                   target_shape_override=target_shape,
                   interference_tolerance=interference_tolerance)

    if node_is_sub_assembly:
        # For sub-assembly targets, treat the entire node as a single unit.
        # Remove individual descendant leaves from the chain output and replace
        # with the original target name as a group.
        resolved_set = set(target_descendants)
        filtered_order = []
        for name in chain_order:
            if name not in resolved_set:
                filtered_order.append(name)
        filtered_order.append(target_name)
        chain_order = filtered_order

        already_inclusion = False
        for d in details:
            if d.get("part") in resolved_set:
                detail_name = d.get("part")
                d["part"] = detail_name
        details.append({
            "part": target_name,
            "stage": len(chain_order),
            "feasible": True,
            "direction": verified_dirs.get(resolved_target, [0, 1, 0]),
            "safe_distance": max_distance,
            "depth": 0,
            "note": "treated as unit ({} leaves from '{}')".format(
                len(target_descendants), target_name),
        })
        for leaf in target_descendants:
            if leaf not in chain_set:
                chain_set.add(leaf)

    stages = [[name] for name in chain_order]
    distance_multipliers = {}
    safe_by_part = {}
    for d in details:
        safe_by_part[d["part"]] = d.get("safe_distance", max_distance)
    for name in chain_order:
        safe_d = safe_by_part.get(name, max_distance)
        distance_multipliers[name] = max(0.05, safe_d / 150.0)

    return stages, verified_dirs, distance_multipliers, details


def _resolve_chain(target_name, part_map, all_part_names,
                   verified_dirs, collision_data, max_distance,
                   chain_order, chain_set, details, depth, max_depth,
                   centroids, skip_obstacles=None,
                   optimize_direction=True,
                   sub_assemblies=None, sa_bbox_cache=None,
                   target_shape_override=None,
                   _in_progress=None,
                   interference_tolerance=0.0):
    """
    Recursively resolve the dependency chain for a target part.

    With optimize_direction=True (default), chooses the disassembly direction
    that minimizes total recursive removal count.

    Args:
        sub_assemblies: assembly hierarchy for spatial obstacle filtering.
        sa_bbox_cache: precomputed compound bbox cache for spatial filtering.
        target_shape_override: if provided, use this shape instead of
            part_map[target_name]["shape"] for collision checking (used for
            sub-assembly targets that are treated as a single rigid body).
        _in_progress: internal set to detect cyclic dependencies.
    """
    if _in_progress is None:
        _in_progress = set()

    if target_name in _in_progress:
        sys.stdout.write("  [depth={}] '{}' deadlock: cyclic dependency detected\n".format(
            depth, target_name))
        sys.stdout.flush()
        details.append({
            "part": target_name, "stage": 0, "feasible": False,
            "depth": depth, "note": "deadlock: cyclic dependency",
        })
        return

    _in_progress.add(target_name)

    if target_name in chain_set:
        _in_progress.discard(target_name)
        return
    if depth > max_depth:
        logger.warning("dependency chain max depth %d reached for %s",
                       max_depth, target_name)
        _in_progress.discard(target_name)
        return

    if target_name not in part_map:
        logger.warning("target '%s' not in part_map, skipping", target_name)
        _in_progress.discard(target_name)
        return

    skip_set = skip_obstacles or set()
    target_shape = target_shape_override if target_shape_override is not None else part_map[target_name]["shape"]

    if optimize_direction:
        sim_cache = {}
        plan = _find_optimal_direction(
            target_name, part_map, all_part_names,
            verified_dirs, collision_data, max_distance,
            chain_set, skip_set, depth, max_depth,
            centroids, sim_cache,
            sub_assemblies=sub_assemblies,
            sa_bbox_cache=sa_bbox_cache,
            target_shape_override=target_shape,
            interference_tolerance=interference_tolerance)

        verified_dirs[target_name] = plan["direction"]

        considered = plan.get("considered", [])
        dir_str = ",".join("{:.2f}".format(x) for x in plan["direction"])
        sys.stdout.write(
            "  [depth={}] '{}' optimal dir=[{}] cost={} depth={} "
            "(considered {} dirs)\n".format(
                depth, target_name, dir_str, plan["cost"],
                plan["chain_depth"], len(considered)))
        sys.stdout.flush()

        if plan["feasible"] and not plan["blockers"]:
            chain_order.append(target_name)
            chain_set.add(target_name)
            details.append({
                "part": target_name,
                "stage": len(chain_order),
                "feasible": True,
                "direction": plan["direction"],
                "safe_distance": plan["safe_distance"],
                "depth": depth,
                "chosen_direction": plan["direction"],
                "considered_directions": considered,
                "expected_chain_cost": plan["cost"],
            })
            _in_progress.discard(target_name)
            return

        for blocker in plan["blockers"]:
            _resolve_chain(blocker, part_map, all_part_names,
                           verified_dirs, collision_data, max_distance,
                           chain_order, chain_set, details,
                           depth + 1, max_depth, centroids,
                           skip_obstacles=skip_set,
                           optimize_direction=optimize_direction,
                           sub_assemblies=sub_assemblies,
                           sa_bbox_cache=sa_bbox_cache,
                           _in_progress=_in_progress,
                           interference_tolerance=interference_tolerance)

        if target_name in chain_set:
            _in_progress.discard(target_name)
            return

        from pipeline.collision_check import check_disassembly_path, filter_obstacles_by_compound_bbox
        recheck_names = [n for n in all_part_names
                         if n != target_name
                         and n not in chain_set
                         and n not in skip_set]
        if sub_assemblies:
            recheck_obstacles = filter_obstacles_by_compound_bbox(
                target_name, target_shape, recheck_names, part_map,
                sub_assemblies, collision_data, max_distance,
                sa_bbox_cache=sa_bbox_cache)
        else:
            recheck_obstacles = [(n, part_map[n]["shape"]) for n in recheck_names]
        recheck = check_disassembly_path(
            target_name, target_shape,
            recheck_obstacles, plan["direction"],
            max_distance, collision_data=collision_data,
            report_all_collisions=True,
            interference_tolerance=interference_tolerance)

        chain_order.append(target_name)
        chain_set.add(target_name)
        residual = [b for b in recheck.get("collision_names", [])
                    if b in part_map and b not in chain_set]
        details.append({
            "part": target_name,
            "stage": len(chain_order),
            "feasible": bool(recheck.get("feasible", False)),
            "direction": plan["direction"],
            "safe_distance": float(recheck.get("max_safe_distance", 0.0)),
            "collision_with": recheck.get("collision_names"),
            "depth": depth,
            "chosen_direction": plan["direction"],
            "considered_directions": considered,
            "expected_chain_cost": plan["cost"],
            "note": "feasible after blocker resolution" if recheck.get("feasible")
                else "deadlock: residual {}".format(residual[:3]),
        })
        _in_progress.discard(target_name)
        return

    from pipeline.collision_check import find_all_blockers, filter_obstacles_by_compound_bbox

    preferred_dir = verified_dirs.get(target_name, [0, 1, 0])
    obstacle_names = [n for n in all_part_names
                     if n != target_name
                     and n not in chain_set
                     and n not in skip_set]
    if sub_assemblies:
        obstacles = filter_obstacles_by_compound_bbox(
            target_name, target_shape, obstacle_names, part_map,
            sub_assemblies, collision_data, max_distance,
            sa_bbox_cache=sa_bbox_cache)
    else:
        obstacles = [(n, part_map[n]["shape"]) for n in obstacle_names]

    sys.stdout.write("  [depth={}] (legacy) resolving '{}' against {} obstacles...\n".format(
        depth, target_name, len(obstacles)))
    sys.stdout.flush()

    result = find_all_blockers(
        target_name, target_shape, obstacles, preferred_dir,
        max_distance, collision_data, max_directions=8,
        interference_tolerance=interference_tolerance)

    verified_dirs[target_name] = result["best_direction"]

    if result["feasible"]:
        chain_order.append(target_name)
        chain_set.add(target_name)
        details.append({
            "part": target_name,
            "stage": len(chain_order),
            "feasible": True,
            "direction": result["best_direction"],
            "safe_distance": result["best_result"]["max_safe_distance"],
            "depth": depth,
        })
        _in_progress.discard(target_name)
        return

    all_blockers = result["blockers"]
    valid_blockers = [b for b in all_blockers
                      if b in part_map
                      and b not in chain_set
                      and b not in skip_set
                      and b != target_name]

    if not valid_blockers:
        chain_order.append(target_name)
        chain_set.add(target_name)
        details.append({
            "part": target_name,
            "stage": len(chain_order),
            "feasible": False,
            "direction": result["best_direction"],
            "safe_distance": result["best_result"]["max_safe_distance"],
            "collision_with": all_blockers,
            "depth": depth,
            "note": "no resolvable blockers, force-removed",
        })
        _in_progress.discard(target_name)
        return

    target_centroid = centroids.get(target_name, np.array([0.0, 0.0, 0.0]))
    def _distance_to_target(blocker_name):
        bc = centroids.get(blocker_name)
        if bc is None:
            return 0.0
        return float(np.linalg.norm(bc - target_centroid))

    valid_blockers.sort(key=_distance_to_target, reverse=True)

    for blocker in valid_blockers:
        _resolve_chain(blocker, part_map, all_part_names,
                       verified_dirs, collision_data, max_distance,
                       chain_order, chain_set, details, depth + 1, max_depth,
                       centroids, skip_obstacles=skip_set,
                       optimize_direction=optimize_direction,
                       _in_progress=_in_progress,
                       interference_tolerance=interference_tolerance)

    if target_name in chain_set:
        _in_progress.discard(target_name)
        return

    recheck_obstacles = [(n, part_map[n]["shape"])
                         for n in all_part_names
                         if n != target_name
                         and n not in chain_set
                         and n not in skip_set]

    recheck = find_all_blockers(
        target_name, target_shape, recheck_obstacles,
        verified_dirs.get(target_name, result["best_direction"]),
        max_distance, collision_data,
        interference_tolerance=interference_tolerance)

    verified_dirs[target_name] = recheck["best_direction"]

    chain_order.append(target_name)
    chain_set.add(target_name)

    if recheck["feasible"]:
        details.append({
            "part": target_name,
            "stage": len(chain_order),
            "feasible": True,
            "direction": recheck["best_direction"],
            "safe_distance": recheck["best_result"]["max_safe_distance"],
            "depth": depth,
            "note": "feasible after resolving blockers",
        })
    else:
        residual = [b for b in recheck["blockers"]
                    if b in part_map and b not in chain_set]
        details.append({
            "part": target_name,
            "stage": len(chain_order),
            "feasible": False,
            "direction": recheck["best_direction"],
            "safe_distance": recheck["best_result"]["max_safe_distance"],
            "collision_with": recheck["blockers"],
            "depth": depth,
            "note": "deadlock: residual blockers {}, force-removed".format(
                residual[:5]),
        })

    _in_progress.discard(target_name)
