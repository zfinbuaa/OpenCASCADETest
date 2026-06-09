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
        tuple: (resolved_name, descendants_set_or_None)
    """
    if target_name in part_map:
        return target_name, None

    if sub_assemblies:
        from pipeline.collision_check import _collect_leaf_descendants
        for sa in sub_assemblies:
            if sa.get("name") == target_name:
                leaves = set()
                _collect_leaf_descendants(target_name, sub_assemblies,
                                          part_map, leaves)
                valid = [n for n in leaves if n in part_map]
                if valid:
                    return valid[0], set(valid)

    suffix_matches = [n for n in part_map
                      if n.endswith(target_name) or target_name in n]
    if suffix_matches:
        suffix_matches.sort(key=len)
        return suffix_matches[0], None

    return target_name, None


def _simulate_dir_blockers(target_name, part_map, all_part_names,
                            chain_set, skip_set, direction, collision_data,
                            max_distance):
    """
    Probe one direction and return ALL parts blocking the path.

    Uses check_disassembly_path with report_all_collisions=True to gather
    every obstacle that intersects the target's swept volume along the
    given direction.

    Returns:
        tuple: (feasible: bool, blockers: list[str], safe_distance: float)
    """
    from pipeline.collision_check import check_disassembly_path

    part = part_map[target_name]
    obstacles = [(n, part_map[n]["shape"])
                 for n in all_part_names
                 if n != target_name
                 and n not in chain_set
                 and n not in skip_set]

    result = check_disassembly_path(
        target_name, part["shape"], obstacles, direction,
        max_distance, collision_data=collision_data,
        report_all_collisions=True)

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


def _find_optimal_direction(target_name, part_map, all_part_names,
                             verified_dirs, collision_data, max_distance,
                             chain_set, skip_set, depth, max_depth,
                             centroids, sim_cache, best_so_far=None):
    """
    Find the optimal disassembly direction for `target_name`.

    Returns the direction that, after recursive resolution, requires the
    FEWEST total parts to be removed (counting target itself).

    Args:
        sim_cache: dict to memoize (part_name, frozenset(available_obstacles)) results
        best_so_far: int upper bound (cost from parent) for pruning

    Returns:
        dict: {
            "feasible": bool,                    # at least one dir works
            "direction": [x,y,z],                # chosen direction
            "blockers": list[str],               # blockers in chosen direction
            "cost": int,                         # total parts (incl. self)
            "chain_depth": int,                  # max recursion depth
            "safe_distance": float,
            "considered": list[dict],            # per-direction summary
        }
    """
    from pipeline.collision_check import find_all_blockers

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

    part = part_map[target_name]
    preferred_dir = verified_dirs.get(target_name, [0, 1, 0])
    obstacles = [(n, part_map[n]["shape"]) for n in avail_key]

    sweep = find_all_blockers(
        target_name, part["shape"], obstacles, preferred_dir,
        max_distance, collision_data)

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
                "blockers_count": 0,
                "chain_cost": 1,
                "selected": fd is chosen,
            })
        result = {
            "feasible": True,
            "direction": chosen["direction"],
            "blockers": [],
            "cost": 1,
            "chain_depth": 1,
            "safe_distance": float(chosen.get("safe_distance", 0)),
            "considered": considered_summary,
        }
        sim_cache[cache_key] = result
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
            "feasible": False, "direction": preferred_dir, "blockers": [],
            "cost": 1, "chain_depth": 1, "safe_distance": 0.0,
            "considered": [], "deadlock": True,
        }
        sim_cache[cache_key] = result
        return result

    best = None

    for cand in candidates:
        true_feasible, true_blockers, true_safe = _simulate_dir_blockers(
            target_name, part_map, all_part_names,
            chain_set, skip_set, cand["direction"], collision_data,
            max_distance)

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
                best_so_far=budget - cumulative_cost)

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
        "blockers": best["blockers"],
        "cost": best["cost"],
        "chain_depth": best["chain_depth"],
        "safe_distance": best["safe_distance"],
        "considered": considered_summary,
    }
    sim_cache[cache_key] = result
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
                              target_name, max_distance=500.0,
                              assembly_centroid=None, sub_assemblies=None,
                              max_recursion=50, optimize_direction=True):
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
            If False, use legacy behavior (recurse on union of all blockers).

    Returns:
        tuple: (stages, verified_directions, distance_multipliers, details)
    """
    part_map = {p["name"]: p for p in parts}

    resolved_target, target_descendants = _resolve_target_node(
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

    verified_dirs = dict(directions)
    chain_order = []
    chain_set = set()
    details = []

    centroids = _precompute_centroids(part_map)

    skip_for_target = set(target_descendants) if target_descendants else set()

    _resolve_chain(resolved_target, part_map, set(part_map.keys()),
                   verified_dirs, collision_data, max_distance,
                   chain_order, chain_set, details, 0, max_recursion,
                   centroids, skip_for_target, optimize_direction)

    if target_descendants:
        for leaf in target_descendants:
            if leaf != resolved_target and leaf not in chain_set:
                chain_order.append(leaf)
                chain_set.add(leaf)
                details.append({
                    "part": leaf,
                    "stage": len(chain_order),
                    "feasible": True,
                    "direction": verified_dirs.get(leaf, [0, 1, 0]),
                    "safe_distance": max_distance,
                    "note": "part of target sub-assembly '{}'".format(target_name),
                })

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
                   optimize_direction=True):
    """
    Recursively resolve the dependency chain for a target part.

    With optimize_direction=True (default), chooses the disassembly direction
    that minimizes total recursive removal count.
    """
    if target_name in chain_set:
        return
    if depth > max_depth:
        logger.warning("dependency chain max depth %d reached for %s",
                       max_depth, target_name)
        return

    if target_name not in part_map:
        logger.warning("target '%s' not in part_map, skipping", target_name)
        return

    skip_set = skip_obstacles or set()

    if optimize_direction:
        sim_cache = {}
        plan = _find_optimal_direction(
            target_name, part_map, all_part_names,
            verified_dirs, collision_data, max_distance,
            chain_set, skip_set, depth, max_depth,
            centroids, sim_cache)

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
            return

        for blocker in plan["blockers"]:
            _resolve_chain(blocker, part_map, all_part_names,
                           verified_dirs, collision_data, max_distance,
                           chain_order, chain_set, details,
                           depth + 1, max_depth, centroids,
                           skip_obstacles=skip_set,
                           optimize_direction=optimize_direction)

        if target_name in chain_set:
            return

        from pipeline.collision_check import check_disassembly_path
        recheck_obstacles = [(n, part_map[n]["shape"])
                             for n in all_part_names
                             if n != target_name
                             and n not in chain_set
                             and n not in skip_set]
        recheck = check_disassembly_path(
            target_name, part_map[target_name]["shape"],
            recheck_obstacles, plan["direction"],
            max_distance, collision_data=collision_data,
            report_all_collisions=True)

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
        return

    from pipeline.collision_check import find_all_blockers

    part = part_map[target_name]
    preferred_dir = verified_dirs.get(target_name, [0, 1, 0])
    obstacles = [(n, part_map[n]["shape"])
                 for n in all_part_names
                 if n != target_name
                 and n not in chain_set
                 and n not in skip_set]

    sys.stdout.write("  [depth={}] (legacy) resolving '{}' against {} obstacles...\n".format(
        depth, target_name, len(obstacles)))
    sys.stdout.flush()

    result = find_all_blockers(
        target_name, part["shape"], obstacles, preferred_dir,
        max_distance, collision_data)

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
                       optimize_direction=optimize_direction)

    if target_name in chain_set:
        return

    recheck_obstacles = [(n, part_map[n]["shape"])
                         for n in all_part_names
                         if n != target_name
                         and n not in chain_set
                         and n not in skip_set]

    recheck = find_all_blockers(
        target_name, part["shape"], recheck_obstacles,
        verified_dirs.get(target_name, result["best_direction"]),
        max_distance, collision_data)

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
