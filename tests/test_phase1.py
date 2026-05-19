"""
Phase 1 tests: Assembly analysis and disassembly sequence generation.

Tests contact detection, fastener identification, DAG building,
and direction calculation using programmatically generated geometry.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_test_parts():
    """Create a small synthetic assembly with known contacts."""
    from OCC.Core.BRepPrimAPI import (
        BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder,
    )
    from OCC.Core.gp import gp_Trsf, gp_Vec
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

    # Base plate (large box at origin)
    base = BRepPrimAPI_MakeBox(100, 5, 50).Shape()

    # Side wall touching base at X=100 face
    wall = BRepPrimAPI_MakeBox(5, 30, 50).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(100, 0, 0))
    wall = BRepBuilderAPI_Transform(wall, trsf).Shape()

    # Small bolt at base-wall intersection so it contacts both
    bolt1 = BRepPrimAPI_MakeCylinder(3, 10).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(99, 5, 25))
    bolt1 = BRepBuilderAPI_Transform(bolt1, trsf).Shape()

    # Another small bolt also at intersection
    bolt2 = BRepPrimAPI_MakeCylinder(3, 10).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(98, 5, 10))
    bolt2 = BRepBuilderAPI_Transform(bolt2, trsf).Shape()

    parts = [
        {"name": "base_plate", "shape": base},
        {"name": "side_wall", "shape": wall},
        {"name": "bolt_A", "shape": bolt1},
        {"name": "bolt_B", "shape": bolt2},
    ]
    return parts


def test_contact_detection():
    """Test that contacts are detected between touching parts."""
    from pipeline.contact_detector import detect_contacts, get_contact_graph

    parts = make_test_parts()
    contacts = detect_contacts(parts)

    assert len(contacts) >= 1, "Expected at least 1 contact"
    print("  [PASS] detect_contacts: {} contacts found".format(len(contacts)))

    # Check specific contacts exist
    pairs = {(c["partA"], c["partB"]) for c in contacts}
    # base_plate should contact side_wall and both bolts
    names = {"base_plate", "side_wall", "bolt_A", "bolt_B"}
    contacted = set()
    for a, b in pairs:
        contacted.add(a)
        contacted.add(b)

    assert "base_plate" in contacted, "base_plate should have contacts"

    graph = get_contact_graph(contacts)
    assert "base_plate" in graph, "base_plate should be in contact graph"
    print("  [PASS] get_contact_graph: {} parts in graph".format(len(graph)))

    for c in contacts:
        assert len(c["avgNormal"]) == 3, "avgNormal should be 3D"
        assert len(c["contactPoints"]) >= 1, "Should have contact points"
    print("  [PASS] contact data structure valid")


def test_contact_no_touching():
    """Test that non-touching parts produce no contact."""
    from pipeline.contact_detector import detect_contacts
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.gp import gp_Trsf, gp_Vec
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

    box1 = BRepPrimAPI_MakeBox(10, 5, 3).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(100, 0, 0))
    box2 = BRepBuilderAPI_Transform(BRepPrimAPI_MakeBox(10, 5, 3).Shape(), trsf).Shape()

    parts = [
        {"name": "box_A", "shape": box1},
        {"name": "box_B", "shape": box2},
    ]
    contacts = detect_contacts(parts)
    assert len(contacts) == 0, "Separated boxes should not contact"
    print("  [PASS] non-touching parts: 0 contacts")


def test_fastener_identification():
    """Test that small parts are identified as fasteners."""
    from pipeline.contact_detector import detect_contacts
    from pipeline.fastener_identifier import identify_fasteners, identify_fasteners_detailed

    parts = make_test_parts()
    contacts = detect_contacts(parts)

    fasteners = identify_fasteners(parts, contacts)
    assert "bolt_A" in fasteners, "bolt_A should be identified as fastener"
    assert "bolt_B" in fasteners, "bolt_B should be identified as fastener"
    assert "base_plate" not in fasteners, "base_plate is too large to be fastener"
    print("  [PASS] identify_fasteners: {} fasteners ({})".format(
        len(fasteners), ", ".join(fasteners)))

    detailed = identify_fasteners_detailed(parts, contacts)
    assert len(detailed) == len(parts), "Should have entry for each part"
    print("  [PASS] identify_fasteners_detailed: {} entries".format(len(detailed)))


def test_dag_building():
    """Test that disassembly DAG is built correctly."""
    from pipeline.contact_detector import detect_contacts
    from pipeline.fastener_identifier import identify_fasteners
    from pipeline.dag_builder import build_disassembly_dag, assign_stages_to_parts

    parts = make_test_parts()
    contacts = detect_contacts(parts)
    fasteners = identify_fasteners(parts, contacts)

    stages = build_disassembly_dag(parts, contacts, fasteners)
    assert len(stages) >= 2, "Should have at least 2 stages"

    all_staged = []
    for s in stages:
        all_staged.extend(s)
    assert len(all_staged) == len(parts), "All parts should be staged"

    print("  [PASS] build_disassembly_dag: {} stages".format(len(stages)))

    stage_map = assign_stages_to_parts(parts, stages)
    assert len(stage_map) == len(parts)
    print("  [PASS] assign_stages_to_parts: {}".format(stage_map))


def test_direction_calculation():
    """Test that disassembly directions are computed."""
    from pipeline.direction_calc import calc_disassembly_direction, compute_all_directions

    parts = make_test_parts()
    contacts = []

    direction = calc_disassembly_direction("base_plate", parts)
    assert len(direction) == 3, "Direction should be [x, y, z]"
    length = sum(x * x for x in direction) ** 0.5
    assert abs(length - 1.0) < 0.01, "Direction should be unit vector"
    print("  [PASS] calc_disassembly_direction for base_plate: {}".format(
        [round(d, 2) for d in direction]))

    all_dirs = compute_all_directions(parts)
    assert len(all_dirs) == len(parts)
    for name, d in all_dirs.items():
        length = sum(x * x for x in d) ** 0.5
        assert abs(length - 1.0) < 0.01, "{} direction not unit".format(name)
    print("  [PASS] compute_all_directions: {} parts".format(len(all_dirs)))


def test_assembly_json():
    """Test assembly.json generation."""
    from pipeline.contact_detector import detect_contacts
    from pipeline.fastener_identifier import identify_fasteners
    from pipeline.dag_builder import build_disassembly_dag
    from pipeline.direction_calc import compute_all_directions
    from pipeline.assembly_json import build_assembly_json, write_assembly_json

    parts = make_test_parts()
    contacts = detect_contacts(parts)
    fasteners = identify_fasteners(parts, contacts)
    directions = compute_all_directions(parts)
    stages = build_disassembly_dag(parts, contacts, fasteners)

    # Attach direction and glbFile to parts
    for part in parts:
        part["direction"] = directions.get(part["name"], [0, 1, 0])
        part["glbFile"] = "parts/{}.glb".format(part["name"])

    assembly = build_assembly_json(parts, stages, "test.stp", contacts, fasteners)

    assert "name" in assembly
    assert "parts" in assembly
    assert "stages" in assembly
    assert "stats" in assembly
    assert assembly["stats"]["totalParts"] == len(parts)
    assert assembly["stats"]["totalFasteners"] == len(fasteners)
    assert assembly["stats"]["totalContacts"] == len(contacts)

    print("  [PASS] build_assembly_json: {} parts, {} stages".format(
        len(assembly["parts"]), len(assembly["stages"])))

    # Test write
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "assembly.json")
        result = write_assembly_json(assembly, out_path)
        assert os.path.exists(result), "JSON file not created"
        assert os.path.getsize(result) > 100, "JSON file too small"
        print("  [PASS] write_assembly_json: {} bytes".format(
            os.path.getsize(result)))


def test_full_phase1_pipeline():
    """Test the complete Phase 1 pipeline end-to-end."""
    from pipeline.contact_detector import detect_contacts
    from pipeline.fastener_identifier import identify_fasteners
    from pipeline.dag_builder import build_disassembly_dag
    from pipeline.direction_calc import compute_all_directions

    parts = make_test_parts()

    contacts = detect_contacts(parts)
    fasteners = identify_fasteners(parts, contacts)
    directions = compute_all_directions(parts)
    stages = build_disassembly_dag(parts, contacts, fasteners)

    # Verify the pipeline produces consistent results
    assert len(contacts) >= 2
    assert len(fasteners) == 2
    assert len(stages) >= 2
    assert len(directions) == len(parts)

    # All parts should appear exactly once across all stages
    all_staged = []
    for s in stages:
        all_staged.extend(s)
    assert set(all_staged) == {p["name"] for p in parts}, "All parts should be staged"
    assert len(all_staged) == len(set(all_staged)), "No duplicates in stages"

    print("  [PASS] full_phase1_pipeline: contacts={}, fasteners={}, stages={}".format(
        len(contacts), len(fasteners), len(stages)))


def main():
    print("=" * 60)
    print("Phase 1 Validation Tests")
    print("=" * 60)

    try:
        test_contact_detection()
        test_contact_no_touching()
        test_fastener_identification()
        test_dag_building()
        test_direction_calculation()
        test_assembly_json()
        test_full_phase1_pipeline()

        print("-" * 60)
        print("ALL PHASE 1 TESTS PASSED")
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
