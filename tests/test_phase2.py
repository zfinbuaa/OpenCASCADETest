"""
Phase 2 tests: Collision detection and path validation.

Tests swept collision checking, multi-direction search,
and full disassembly plan validation.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_test_assembly():
    """Create known assembly: base + plug that fits into a hole."""
    from OCC.Core.BRepPrimAPI import (
        BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder,
    )
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCC.Core.gp import gp_Trsf, gp_Vec
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

    # Base block with a hole
    base = BRepPrimAPI_MakeBox(50, 20, 30).Shape()
    hole = BRepPrimAPI_MakeCylinder(5, 25).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(25, -2, 15))
    hole = BRepBuilderAPI_Transform(hole, trsf).Shape()
    base = BRepAlgoAPI_Cut(base, hole).Shape()

    # Plug that fits into the hole (cylinder sitting in it)
    plug = BRepPrimAPI_MakeCylinder(4.9, 20).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(25, 0, 15))
    plug = BRepBuilderAPI_Transform(plug, trsf).Shape()

    # Another part on top
    top = BRepPrimAPI_MakeBox(50, 5, 30).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(0, 20, 0))
    top = BRepBuilderAPI_Transform(top, trsf).Shape()

    parts = [
        {"name": "base", "shape": base},
        {"name": "plug", "shape": plug},
        {"name": "top_cover", "shape": top},
    ]
    return parts


def test_collision_no_obstacle():
    """Moving in empty space should always succeed."""
    from pipeline.collision_check import check_disassembly_path
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

    box = BRepPrimAPI_MakeBox(10, 5, 3).Shape()
    result = check_disassembly_path(box, [], [0, 1, 0], 100, 10)

    assert result["feasible"], "Should be feasible in empty space"
    assert result["max_safe_distance"] == 100
    print("  [PASS] no_obstacle: feasible={}, safe={}".format(
        result["feasible"], result["max_safe_distance"]))


def test_collision_blocked():
    """Moving into another part should detect collision."""
    from pipeline.collision_check import check_disassembly_path
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Trsf, gp_Vec
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

    box = BRepPrimAPI_MakeBox(10, 5, 3).Shape()

    # Blocking wall 5mm above box
    wall = BRepPrimAPI_MakeBox(10, 1, 3).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(0, 7, 0))
    wall = BRepBuilderAPI_Transform(wall, trsf).Shape()

    obstacles = [("wall", wall)]
    result = check_disassembly_path(box, obstacles, [0, 1, 0], 20, 20)

    assert not result["feasible"], "Should detect collision"
    assert result["collision_with"] == "wall"
    print("  [PASS] blocked: collision at step {}, with {}".format(
        result["collision_at_step"], result["collision_with"]))


def test_collision_safe_distance():
    """Moving away from obstacles should be safe."""
    from pipeline.collision_check import check_disassembly_path
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Trsf, gp_Vec
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

    box = BRepPrimAPI_MakeBox(10, 5, 3).Shape()

    # Wall 50mm above box — moving 20mm up is safe
    wall = BRepPrimAPI_MakeBox(10, 1, 3).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(0, 60, 0))
    wall = BRepBuilderAPI_Transform(wall, trsf).Shape()

    obstacles = [("wall", wall)]
    result = check_disassembly_path(box, obstacles, [0, 1, 0], 40, 20)

    assert result["feasible"], "40mm gap should be feasible"
    print("  [PASS] safe_distance: feasible={}".format(result["feasible"]))


def test_path_searcher():
    """Test multi-direction search finds a feasible path."""
    from pipeline.collision_check import check_disassembly_path
    from pipeline.path_searcher import find_feasible_direction
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Trsf, gp_Vec
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

    box = BRepPrimAPI_MakeBox(10, 5, 3).Shape()

    # Block +Y direction (wall is above)
    wall = BRepPrimAPI_MakeBox(10, 1, 3).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(0, 6, 0))
    wall = BRepBuilderAPI_Transform(wall, trsf).Shape()

    obstacles = [("wall", wall)]

    # Preferred direction is +Y (blocked), should find another
    result = find_feasible_direction(box, obstacles, [0, 1, 0], 50, 10)

    assert result["direction"] is not None, "Should find some direction"
    assert result["result"]["feasible"], "Found direction should be feasible"
    print("  [PASS] path_searcher: found dir={}, feasible={}".format(
        [round(d, 2) for d in result["direction"]],
        result["result"]["feasible"]))


def test_path_validation():
    """Test full disassembly plan validation."""
    from pipeline.path_validator import validate_disassembly_plan, generate_report

    parts = make_test_assembly()

    # Mock direction map: plug should come out along +Y, top_cover +Y, base -Y
    directions = {
        "plug": [0, 1, 0],
        "top_cover": [0, 1, 0],
        "base": [0, -1, 0],
    }

    stages = [["plug"], ["top_cover"], ["base"]]

    validation = validate_disassembly_plan(parts, stages, directions, 100)

    assert "valid" in validation
    assert validation["total_parts"] == 3
    print("  [PASS] validate: {} total, {} feasible, {} blocked".format(
        validation["total_parts"], validation["feasible_parts"],
        validation["blocked_parts"]))

    report = generate_report(validation)
    assert "PASS" in report or "FAIL" in report
    print("  [PASS] generate_report: {} chars".format(len(report)))


def main():
    print("=" * 60)
    print("Phase 2 Validation Tests")
    print("=" * 60)

    try:
        test_collision_no_obstacle()
        test_collision_blocked()
        test_collision_safe_distance()
        test_path_searcher()
        test_path_validation()

        print("-" * 60)
        print("ALL PHASE 2 TESTS PASSED")
        return 0

    except ImportError as e:
        print("\n[SKIP] OCCT not installed: {}".format(e))
        return 1
    except Exception as e:
        print("\n[FAIL] {}: {}".format(type(e).__name__, e))
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
