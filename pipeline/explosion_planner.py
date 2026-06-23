"""
Explosion view planner — geometric-only, no swept collision check.

Generates explosion direction/distance/stage for each part based on:
  1. Assembly constraint direction (reused from direction_calc)
  2. Distance scaled by hierarchy depth and distance-to-center
  3. Endpoint AABB overlap detection with iterative correction
  4. Stage assignment by distance-to-center bucketing

The output tuple shape matches build_disassembly_dag_v2 for drop-in use.
"""

import logging
import sys
import time
import numpy as np

from pipeline.direction_calc import (
    CANDIDATE_DIRS, _compute_centroids, _compute_assembly_centroid,
)

logger = logging.getLogger(__name__)


def _hierarchy_depth_map(parts):
    """Return {name: depth} based on ancestors list length."""
    depths = {}
    for p in parts:
        depths[p["name"]] = len(p.get("ancestors", []) or [])
    return depths


def _resolve_center(center_part, part_map, sub_assemblies, centroids,
                    assembly_centroid):
    """Resolve center_part argument into (center_xyz, fixed_part_names_set,
    primary_center_name).

    primary_center_name is the single name that gets isExplosionCenter=True
    flag in the output (may be None when no center specified).
    """
    if not center_part:
        return assembly_centroid, set(), None

    # Case 1: center_part is a leaf in part_map
    if center_part in part_map:
        c = centroids.get(center_part)
        if c is None:
            c = assembly_centroid
        return c, {center_part}, center_part

    # Case 2: center_part is a sub-assembly node — collect all leaf descendants
    if sub_assemblies:
        from pipeline.collision_check import _collect_leaf_descendants
        leaves = set()
        try:
            _collect_leaf_descendants(center_part, sub_assemblies, part_map,
                                      leaves, memo={})
        except Exception as e:
            logger.warning("failed to expand sub-assembly center %r: %s",
                           center_part, e)
            leaves = set()
        valid = {n for n in leaves if n in part_map}
        if valid:
            # compute aggregate centroid from member leaf centroids
            pts = [centroids[n] for n in valid if centroids.get(n) is not None]
            if pts:
                c = np.mean(pts, axis=0)
            else:
                c = assembly_centroid
            return c, valid, center_part

    # Case 3: unresolved — log and fall back to assembly_centroid
    logger.warning("center_part %r could not be resolved; using assembly centroid",
                   center_part)
    return assembly_centroid, set(), None


def _aabb_volume(amin, amax):
    if amin is None or amax is None:
        return 0.0
    d = amax - amin
    return float(d[0] * d[1] * d[2])


def _aabb_overlap(a_min, a_max, b_min, b_max):
    """True if two AABBs strictly overlap (non-zero intersection)."""
    if a_min is None or b_min is None:
        return False
    return bool(np.all(a_min < b_max) and np.all(a_max > b_min))


def _aabb_pair_separation(a_min, a_max, b_min, b_max):
    """If overlapping, return the separation vector center_b - center_a (3-vector).
    Used to choose escape direction."""
    ca = (a_min + a_max) * 0.5
    cb = (b_min + b_max) * 0.5
    return cb - ca


def _find_overlap_pairs(endpoint_aabbs):
    """Find all overlapping AABB pairs via X-axis sweep.

    Args:
        endpoint_aabbs: list of (name, amin, amax) — entries with amin=None skipped.
    Returns:
        set of frozenset({name_a, name_b}) of overlapping pairs.
    """
    valid = [(n, amin, amax) for (n, amin, amax) in endpoint_aabbs
             if amin is not None and amax is not None]
    if len(valid) < 2:
        return set()

    # Sort by min_x ascending
    valid.sort(key=lambda t: t[1][0])
    pairs = set()
    n = len(valid)
    for i in range(n):
        ni, amin_i, amax_i = valid[i]
        for j in range(i + 1, n):
            nj, amin_j, amax_j = valid[j]
            if amin_j[0] >= amax_i[0]:
                break  # X disjoint, all further j also disjoint in X
            if (amin_i[1] < amax_j[1] and amax_i[1] > amin_j[1] and
                    amin_i[2] < amax_j[2] and amax_i[2] > amin_j[2]):
                pairs.add(frozenset((ni, nj)))
    return pairs


def _compute_endpoint_aabb(base_min, base_max, direction, distance):
    """Translate base AABB by direction*distance."""
    if base_min is None or base_max is None:
        return None, None
    offset = np.array(direction, dtype=np.float64) * float(distance)
    return base_min + offset, base_max + offset


def _pick_max_separation_dir(current_dir, escape_vec, candidates):
    """Pick the candidate direction that maximizes alignment with escape_vec
    while being different from current_dir.

    escape_vec: 3-vector pointing from the conflicting other-part to self.
    Higher dot(cand, escape) means better separation.
    """
    cur = np.array(current_dir, dtype=np.float64)
    cur_norm = np.linalg.norm(cur)
    if cur_norm > 1e-10:
        cur_unit = cur / cur_norm
    else:
        cur_unit = None

    esc_norm = np.linalg.norm(escape_vec)
    if esc_norm < 1e-10:
        # Degenerate — pick any non-current candidate
        for cand in candidates:
            if cur_unit is None or float(np.dot(cur_unit, cand)) < 0.99:
                return cand.tolist()
        return candidates[0].tolist()
    escape_unit = escape_vec / esc_norm

    best = None
    best_score = -float('inf')
    for cand in candidates:
        # Skip the (near) current direction
        if cur_unit is not None and float(np.dot(cur_unit, cand)) > 0.99:
            continue
        score = float(np.dot(escape_unit, cand))
        if score > best_score:
            best_score = score
            best = cand
    if best is None:
        return candidates[0].tolist()
    return best.tolist()


def build_explosion_plan(parts, directions, collision_data,
                         sub_assemblies=None,
                         center_part=None,
                         max_distance=500.0,
                         base_explosion_distance=150.0,
                         n_stage_buckets=4,
                         max_correction_iters=3,
                         distance_growth_factor=1.2):
    """Build a geometric-only explosion plan.

    Args:
        parts: list of part dicts with 'name','shape','transform','parent','ancestors'.
        directions: dict[name -> [x,y,z]] from compute_all_directions().
        collision_data: dict[name -> MeshCollisionData] (used for aabb_min/max only,
            no swept collision).
        sub_assemblies: list of sub-assembly dicts (for resolving sub-assembly center).
        center_part: str or None. Name of a leaf part or sub-assembly to fix as
            explosion center. If None, uses geometric assembly centroid.
        max_distance: hard cap on per-part travel distance (mm).
        base_explosion_distance: animation base distance (mm); multipliers are
            distance/base_explosion_distance so the front-end animation scales as
            expected (matches dag_builder semantics).
        n_stage_buckets: number of stage buckets (1..N) by distance-to-center.
        max_correction_iters: max iterations for overlap correction loop.
        distance_growth_factor: distance multiplier per correction attempt.

    Returns:
        tuple: (stages, verified_directions, distance_multipliers, details)
            stages: list[list[str]] — buckets ordered outer→inner. Fixed center
                parts are NOT included (they have stage=0).
            verified_directions: dict[name -> [x,y,z]] possibly mutated by overlap fix.
            distance_multipliers: dict[name -> float] = distance/base_explosion_distance.
            details: list of per-part dicts:
                {part, stage, feasible, direction, safe_distance,
                 isExplosionCenter, corrections, overlaps_accepted}
    """
    t_start = time.time()

    part_map = {p["name"]: p for p in parts}
    part_names = [p["name"] for p in parts]

    if not part_names:
        return [], {}, {}, []

    centroids = _compute_centroids(parts)
    assembly_centroid = _compute_assembly_centroid(parts, centroids)

    center_xyz, fixed_parts, primary_center_name = _resolve_center(
        center_part, part_map, sub_assemblies, centroids, assembly_centroid)

    sys.stdout.write(
        "  Explosion center: {}\n".format(
            "user='{}' ({} fixed leaves)".format(center_part, len(fixed_parts))
            if center_part else "geometric centroid"))
    sys.stdout.flush()

    # ── Phase A: initial direction & distance ──────────────────
    depths = _hierarchy_depth_map(parts)
    max_depth = max(depths.values()) if depths else 1

    # scene radius from center
    dist_to_center = {}
    for name in part_names:
        c = centroids.get(name)
        if c is None:
            dist_to_center[name] = 0.0
        else:
            dist_to_center[name] = float(np.linalg.norm(c - center_xyz))
    scene_radius = max(dist_to_center.values()) if dist_to_center else 1.0
    if scene_radius < 1e-6:
        scene_radius = 1.0

    verified_dirs = {}
    distances = {}
    corrections_log = {name: [] for name in part_names}
    is_center_flag = {}

    for name in part_names:
        if name in fixed_parts:
            verified_dirs[name] = [0.0, 0.0, 0.0]
            distances[name] = 0.0
            is_center_flag[name] = (name == primary_center_name) or (
                primary_center_name is None and False)
            # For sub-assembly center, mark only the first leaf as primary
            # (front-end may flag all members; we flag the primary for clarity)
            continue
        d = directions.get(name)
        if not d or len(d) != 3:
            d = [0.0, 1.0, 0.0]
        verified_dirs[name] = list(d)
        depth_factor = 1.0 + (depths.get(name, 0) / max(max_depth, 1)) * 0.5
        dist_factor = 1.0 + (dist_to_center.get(name, 0.0) / scene_radius)
        dist = base_explosion_distance * depth_factor * dist_factor
        if dist > max_distance:
            dist = max_distance
        distances[name] = float(dist)
        is_center_flag[name] = False

    # Mark sub-assembly center members (all share isExplosionCenter=True for fronend grouping)
    if center_part and fixed_parts:
        if primary_center_name and primary_center_name in part_map:
            # leaf center: only this one part
            is_center_flag[primary_center_name] = True
        else:
            # sub-assembly: flag every member
            for n in fixed_parts:
                is_center_flag[n] = True

    # ── Phase B: endpoint AABB overlap detection & correction ──
    def _endpoint_aabb(name):
        cd = collision_data.get(name) if collision_data else None
        if cd is None or cd.aabb_min is None or cd.aabb_max is None:
            return None, None
        if name in fixed_parts:
            return cd.aabb_min.copy(), cd.aabb_max.copy()
        return _compute_endpoint_aabb(cd.aabb_min, cd.aabb_max,
                                      verified_dirs[name], distances[name])

    endpoint = {}
    for name in part_names:
        amin, amax = _endpoint_aabb(name)
        endpoint[name] = (amin, amax)

    overlap_iter = 0
    total_resolved = 0
    total_accepted = 0
    unresolved_pairs = set()

    while overlap_iter < max_correction_iters:
        aabb_list = [(n, e[0], e[1]) for n, e in endpoint.items()]
        pairs = _find_overlap_pairs(aabb_list)
        if not pairs:
            break

        sys.stdout.write(
            "  Overlap fix iter {}: {} overlapping pair(s)\n".format(
                overlap_iter + 1, len(pairs)))
        sys.stdout.flush()

        progress_made = False
        for pair in list(pairs):
            a, b = tuple(pair)
            # Skip pairs where either side is the fixed center (we can't move it)
            a_fixed = a in fixed_parts
            b_fixed = b in fixed_parts
            if a_fixed and b_fixed:
                unresolved_pairs.add(pair)
                continue

            # Choose target = smaller volume (non-fixed)
            if a_fixed:
                target, other = b, a
            elif b_fixed:
                target, other = a, b
            else:
                va = _aabb_volume(*endpoint[a])
                vb = _aabb_volume(*endpoint[b])
                target, other = (a, b) if va <= vb else (b, a)

            tmin, tmax = endpoint[target]
            omin, omax = endpoint[other]
            if tmin is None or omin is None:
                unresolved_pairs.add(pair)
                continue

            escape_vec = _aabb_pair_separation(omin, omax, tmin, tmax)
            # escape_vec points from other → target; we want target to move further along this

            resolved_for_this_pair = False

            # Attempt 1: grow distance ×factor
            new_dist = distances[target] * distance_growth_factor
            if new_dist > max_distance:
                new_dist = max_distance
            if new_dist > distances[target] + 1e-6:
                trial_min, trial_max = _compute_endpoint_aabb(
                    collision_data[target].aabb_min,
                    collision_data[target].aabb_max,
                    verified_dirs[target], new_dist)
                if not _aabb_overlap(trial_min, trial_max, omin, omax):
                    distances[target] = new_dist
                    endpoint[target] = (trial_min, trial_max)
                    corrections_log[target].append({
                        "action": "grow_dist", "new_distance": new_dist,
                        "vs": other, "iter": overlap_iter + 1,
                    })
                    resolved_for_this_pair = True
                    progress_made = True
                    total_resolved += 1

            # Attempt 2: flip direction
            if not resolved_for_this_pair:
                new_dir = _pick_max_separation_dir(
                    verified_dirs[target], escape_vec, CANDIDATE_DIRS)
                trial_min, trial_max = _compute_endpoint_aabb(
                    collision_data[target].aabb_min,
                    collision_data[target].aabb_max,
                    new_dir, distances[target])
                if not _aabb_overlap(trial_min, trial_max, omin, omax):
                    verified_dirs[target] = new_dir
                    endpoint[target] = (trial_min, trial_max)
                    corrections_log[target].append({
                        "action": "flip_dir", "new_direction": new_dir,
                        "vs": other, "iter": overlap_iter + 1,
                    })
                    resolved_for_this_pair = True
                    progress_made = True
                    total_resolved += 1

            if not resolved_for_this_pair:
                unresolved_pairs.add(pair)

        overlap_iter += 1
        if not progress_made:
            break

    # Final pass: count surviving overlaps
    final_aabb_list = [(n, e[0], e[1]) for n, e in endpoint.items()]
    final_overlaps = _find_overlap_pairs(final_aabb_list)
    total_accepted = len(final_overlaps)

    # ── Phase C: stage bucketing by distance to center ──────────
    non_fixed = [n for n in part_names if n not in fixed_parts]
    if non_fixed:
        d_values = sorted((dist_to_center[n] for n in non_fixed), reverse=True)
        # buckets: outer first, so larger distance = lower stage number
        n_buckets = max(1, min(n_stage_buckets, len(d_values)))
        # Quantile cuts: stage k for k in 1..n_buckets, evenly split
        cuts = []
        for k in range(1, n_buckets):
            idx = int(round(len(d_values) * k / n_buckets))
            idx = max(0, min(len(d_values) - 1, idx))
            cuts.append(d_values[idx])
        # cuts is descending list of thresholds
    else:
        n_buckets = 1
        cuts = []

    def _bucket_for(name):
        if name in fixed_parts:
            return 0
        if not cuts:
            return 1
        d = dist_to_center.get(name, 0.0)
        for i, threshold in enumerate(cuts):
            if d >= threshold:
                return i + 1
        return len(cuts) + 1

    stage_assignment = {name: _bucket_for(name) for name in part_names}

    # Build stages list (only stages 1..n_buckets, exclude stage 0)
    stages = [[] for _ in range(n_buckets)]
    for name in part_names:
        s = stage_assignment[name]
        if s >= 1:
            stages[s - 1].append(name)
    # Drop trailing empties
    while stages and not stages[-1]:
        stages.pop()

    # ── Distance multipliers ────────────────────────────────────
    distance_multipliers = {}
    for name in part_names:
        if name in fixed_parts:
            distance_multipliers[name] = 0.0
        else:
            mult = distances[name] / max(base_explosion_distance, 1e-6)
            distance_multipliers[name] = max(0.05, float(mult))

    # ── Details ─────────────────────────────────────────────────
    details = []
    for name in part_names:
        details.append({
            "part": name,
            "stage": stage_assignment[name],
            "feasible": True,
            "direction": verified_dirs[name],
            "safe_distance": distances[name],
            "isExplosionCenter": bool(is_center_flag.get(name, False)),
            "corrections": corrections_log[name],
        })

    elapsed = time.time() - t_start
    sys.stdout.write(
        "  Explosion plan built in {:.2f}s — {} parts, {} stages, "
        "{} overlaps resolved, {} accepted\n".format(
            elapsed, len(parts), len(stages),
            total_resolved, total_accepted))
    sys.stdout.flush()

    return stages, verified_dirs, distance_multipliers, details
