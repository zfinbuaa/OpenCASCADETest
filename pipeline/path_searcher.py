"""
Multi-direction disassembly path searcher.

Tries a structured set of candidate directions to find a feasible
disassembly path for a given part among obstacles.
"""

import itertools
from .collision_check import check_disassembly_path


def find_feasible_direction(part_name, part_shape, other_shapes, preferred_dir,
                            max_distance=500.0, steps=20,
                            min_safe_fraction=0.5,
                            collision_data=None):
    """
    Search for a feasible disassembly direction.

    Args:
        part_name: string name of the part.
        part_shape: TopoDS_Shape to move.
        other_shapes: list of (name, TopoDS_Shape) obstacles.
        preferred_dir: [x, y, z] initial guess.
        max_distance: total movement distance to check.
        steps: discretization steps.
        min_safe_fraction: minimum fraction of max_distance required.
        collision_data: optional pre-computed collision data dict.

    Returns:
        dict: {
            direction: [x,y,z] or None,
            result: collision_check result dict,
        }
    """
    candidates = _generate_candidates(preferred_dir)

    best = None
    best_distance = 0

    for direction in candidates:
        result = check_disassembly_path(
            part_name, part_shape, other_shapes, direction, max_distance, steps,
            collision_data=collision_data)

        safe_dist = result["max_safe_distance"]
        threshold = max_distance * min_safe_fraction

        if result["feasible"]:
            return {
                "direction": direction,
                "result": result,
            }

        if safe_dist > best_distance:
            best_distance = safe_dist
            best = {
                "direction": direction,
                "result": result,
            }

    if best is None:
        return {"direction": None, "result": None}

    return best


def _generate_candidates(preferred_dir):
    """Generate an ordered list of candidate direction vectors."""
    candidates = []

    candidates.append(list(preferred_dir))

    axes = [
        [1, 0, 0], [-1, 0, 0],
        [0, 1, 0], [0, -1, 0],
        [0, 0, 1], [0, 0, -1],
    ]
    for axis in axes:
        if axis != list(preferred_dir):
            candidates.append(axis)

    for signs in itertools.product([-1, 1], repeat=3):
        d = list(signs)
        length = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]) ** 0.5
        candidates.append([d[0] / length, d[1] / length, d[2] / length])

    face_diag = [
        [1, 1, 0], [1, -1, 0], [-1, 1, 0], [-1, -1, 0],
        [1, 0, 1], [1, 0, -1], [-1, 0, 1], [-1, 0, -1],
        [0, 1, 1], [0, 1, -1], [0, -1, 1], [0, -1, -1],
    ]
    for d in face_diag:
        length = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]) ** 0.5
        candidates.append([d[0] / length, d[1] / length, d[2] / length])

    return candidates


def compute_all_feasible_directions(parts, contacts, directions,
                                    max_distance=500.0,
                                    collision_data=None):
    """
    For each part, find a feasible disassembly direction.

    Args:
        parts: list of part dicts with 'name' and 'shape'.
        contacts: contact list from detect_contacts().
        directions: dict of name -> [x,y,z] from compute_all_directions().
        max_distance: movement distance in mm.
        collision_data: optional pre-computed collision data.

    Returns:
        dict[str, dict]: name -> {direction, result}
    """
    feasible = {}

    for part in parts:
        name = part["name"]
        shape = part["shape"]
        preferred = directions.get(name, [0, 1, 0])

        others = [(p["name"], p["shape"]) for p in parts if p["name"] != name]

        found = find_feasible_direction(
            name, shape, others, preferred, max_distance,
            collision_data=collision_data)
        feasible[name] = found

    return feasible
