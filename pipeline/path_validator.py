"""
Path validation and reporting.

Validates the complete disassembly plan by checking each part's
path against the remaining obstacles at each stage.
Uses pre-computed mesh collision data for performance.
"""


def validate_disassembly_plan(parts, stages, directions, max_distance=500.0,
                              progress_callback=None, collision_data=None):
    """
    Validate the entire disassembly plan stage-by-stage.

    For each stage, checks that parts can be removed without colliding
    with parts that remain after they're removed.

    Args:
        parts: list of part dicts with 'name' and 'shape'.
        stages: list of stage-lists from build_disassembly_dag().
        directions: dict of name -> [x,y,z] unit vectors.
        max_distance: movement distance in mm.
        progress_callback: optional callback(done, total, name).
        collision_data: optional pre-computed dict from prepare_collision_data().

    Returns:
        dict: {
            valid: bool,
            total_parts: int,
            feasible_parts: int,
            blocked_parts: int,
            details: list of per-part results,
        }
    """
    from .collision_check import check_disassembly_path, prepare_collision_data

    part_map = {p["name"]: p for p in parts}
    removed = set()
    results = []

    if collision_data is None:
        collision_data = prepare_collision_data(parts)

    for stage_idx, stage_parts in enumerate(stages):
        for name in stage_parts:
            part = part_map.get(name)
            if part is None:
                results.append({
                    "part": name,
                    "stage": stage_idx + 1,
                    "feasible": False,
                    "error": "Part not found",
                    "safe_distance": 0.0,
                })
                continue

            direction = directions.get(name, [0, 1, 0])
            shape = part["shape"]

            obstacles = []
            for other_name, other_part in part_map.items():
                if other_name != name and other_name not in removed:
                    obstacles.append((other_name, other_part["shape"]))

            result = check_disassembly_path(
                shape, obstacles, direction, max_distance,
                collision_data=collision_data)

            results.append({
                "part": name,
                "stage": stage_idx + 1,
                "feasible": result["feasible"],
                "safe_distance": result["max_safe_distance"],
                "collision_with": result.get("collision_with"),
            })

            removed.add(name)

            if progress_callback:
                progress_callback(len(removed), len(parts), name)

    feasible_count = sum(1 for r in results if r["feasible"])
    blocked_count = len(results) - feasible_count

    return {
        "valid": blocked_count == 0,
        "total_parts": len(results),
        "feasible_parts": feasible_count,
        "blocked_parts": blocked_count,
        "details": results,
    }


def generate_report(validation_result):
    """
    Generate a human-readable validation report.

    Args:
        validation_result: dict from validate_disassembly_plan().

    Returns:
        str: Multi-line report string.
    """
    lines = []
    lines.append("=" * 60)
    lines.append("Disassembly Path Validation Report")
    lines.append("=" * 60)
    lines.append("Total parts: {}".format(validation_result["total_parts"]))
    lines.append("Feasible:    {}".format(validation_result["feasible_parts"]))
    lines.append("Blocked:     {}".format(validation_result["blocked_parts"]))
    lines.append("Overall:     {}".format(
        "PASS" if validation_result["valid"] else "FAIL"))
    lines.append("-" * 60)

    for detail in validation_result["details"]:
        status = "OK" if detail["feasible"] else "BLOCKED"
        line = "  [{}] Stage {:2d} | {:20s}".format(
            status, detail["stage"], detail["part"])
        if not detail["feasible"] and detail.get("collision_with"):
            line += " | collision: {}".format(detail["collision_with"])
        safe = detail.get("safe_distance", 0.0)
        line += " | safe: {:.1f}mm".format(safe)
        lines.append(line)

    lines.append("-" * 60)
    return "\n".join(lines)
