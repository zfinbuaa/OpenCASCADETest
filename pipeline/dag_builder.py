"""
Disassembly Directed Acyclic Graph (DAG) builder.

Constructs a DAG from contact relationships and produces ordered
disassembly stages via topological sort.

Direction-aware: Uses disassembly directions to determine which parts
block which, creating a directed graph instead of the naive bidirectional
approach. If part A's removal direction points toward part B, then B
blocks A (A cannot be removed without first removing or moving past B).

Rules:
  1. Fasteners are removed first (stage 1).
  2. After fasteners removed, parts with zero remaining blockers are next.
  3. Continue BFS-like: at each stage, remove all parts that no longer
     have any blocking contacts.
  4. Deadlock: if no zero-blocker parts remain, pick the part with the
     fewest remaining blockers as a forced-removal candidate.
"""

import numpy as np
from collections import deque


def _determine_blocking(contacts, directions):
    """
    Build direction-aware blocking relationships.

    Part B blocks part A if:
    - A and B are in contact, AND
    - A's disassembly direction points toward B's centroid relative to A's.

    If direction information is unavailable or ambiguous, fall back to
    bidirectional blocking (both directions).

    Returns:
        dict[str, set]: part_name -> set of part_names that block it.
    """
    blocked_by = {}

    for c in contacts:
        a = c["partA"]
        b = c["partB"]

        blocked_by.setdefault(a, set())
        blocked_by.setdefault(b, set())

        avg_normal = np.array(c.get("avgNormal", [0, 0, 0]), dtype=np.float64)
        area = c.get("contactArea", 1.0)

        dir_a = directions.get(a)
        dir_b = directions.get(b)

        if dir_a is not None and dir_b is not None:
            da = np.array(dir_a, dtype=np.float64)
            da_norm = np.linalg.norm(da)

            db = np.array(dir_b, dtype=np.float64)
            db_norm = np.linalg.norm(db)

            if da_norm > 1e-10 and db_norm > 1e-10:
                da_hat = da / da_norm
                db_hat = db / db_norm

                normal_a_to_b = avg_normal.copy()
                n_norm = np.linalg.norm(normal_a_to_b)
                if n_norm > 1e-10:
                    normal_a_to_b /= n_norm

                alignment_a = float(np.dot(da_hat, normal_a_to_b))
                if alignment_a > -0.3:
                    blocked_by[a].add(b)

                alignment_b = float(np.dot(db_hat, -normal_a_to_b))
                if alignment_b > -0.3:
                    blocked_by[b].add(a)

                if abs(alignment_a) < 0.3 and abs(alignment_b) < 0.3:
                    blocked_by[a].add(b)
                    blocked_by[b].add(a)
            else:
                blocked_by[a].add(b)
                blocked_by[b].add(a)
        else:
            blocked_by[a].add(b)
            blocked_by[b].add(a)

    return blocked_by


def build_disassembly_dag(parts, contacts, fasteners=None, directions=None):
    """
    Build disassembly stages from part-contact graph with direction awareness.

    Args:
        parts: list of part dicts with 'name'.
        contacts: list of contact dicts from detect_contacts().
        fasteners: list of part name strings to remove first (optional).
        directions: dict of name -> [x,y,z] unit vectors (optional).
                    If provided, used to determine directional blocking.

    Returns:
        list[list[str]]: Each inner list is a stage containing part names
                         to be removed in that order.
    """
    if fasteners is None:
        fasteners = []

    part_names = [p["name"] for p in parts]
    fastener_set = set(fasteners)

    for name in part_names:
        pass

    if directions is not None:
        blocked_by = _determine_blocking(contacts, directions)
    else:
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
    """
    Assign a stage number to each part based on the disassembly stages.

    Args:
        parts: list of part dicts with 'name'.
        stages: list of stage lists from build_disassembly_dag().

    Returns:
        dict[str, int]: part_name -> stage_number (1-indexed)
    """
    stage_map = {}
    for idx, stage_parts in enumerate(stages):
        for name in stage_parts:
            stage_map[name] = idx + 1
    return stage_map
