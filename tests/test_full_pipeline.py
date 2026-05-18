"""
End-to-end pipeline tests — runs complete STP-less pipeline.

Validates the full chain from synthetic geometry through to
assembly.json output and collision report.
"""

import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_assembly_parts():
    """Create a multi-part synthetic assembly."""
    from OCC.Core.BRepPrimAPI import (
        BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder,
    )
    from OCC.Core.gp import gp_Trsf, gp_Vec
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

    # Base plate
    base = BRepPrimAPI_MakeBox(100, 5, 50).Shape()

    # Side wall
    wall = BRepPrimAPI_MakeBox(5, 30, 50).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(100, 0, 0))
    wall = BRepBuilderAPI_Transform(wall, trsf).Shape()

    # Small bolts on base (not touching wall)
    bolt1 = BRepPrimAPI_MakeCylinder(3, 10).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(30, 5, 25))
    bolt1 = BRepBuilderAPI_Transform(bolt1, trsf).Shape()

    bolt2 = BRepPrimAPI_MakeCylinder(3, 10).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(70, 5, 25))
    bolt2 = BRepBuilderAPI_Transform(bolt2, trsf).Shape()

    parts = [
        {"name": "base_plate", "shape": base},
        {"name": "side_wall", "shape": wall},
        {"name": "bolt_A", "shape": bolt1},
        {"name": "bolt_B", "shape": bolt2},
    ]
    return parts


def test_full_pipeline():
    """Test the complete Phase 0-2 pipeline without STP file."""
    from pipeline.contact_detector import detect_contacts
    from pipeline.fastener_identifier import identify_fasteners
    from pipeline.dag_builder import build_disassembly_dag
    from pipeline.direction_calc import compute_all_directions
    from pipeline.assembly_json import build_assembly_json, write_assembly_json
    from pipeline.gltf_exporter import export_assembly_indexed
    from pipeline.path_validator import validate_disassembly_plan, generate_report

    parts = make_assembly_parts()

    # Phase 1: Analysis
    contacts = detect_contacts(parts)
    fasteners = identify_fasteners(parts, contacts)
    directions = compute_all_directions(parts, contacts)
    stages = build_disassembly_dag(parts, contacts, fasteners, directions)

    assert len(contacts) >= 1, "Should detect contacts"
    assert len(stages) >= 1, "Should have stages"
    assert len(directions) == len(parts)

    # Export glb (side effect: adds glbFile)
    with tempfile.TemporaryDirectory() as tmpdir:
        parts_dir = os.path.join(tmpdir, "parts")
        parts = export_assembly_indexed(parts, parts_dir)

        for part in parts:
            assert "glbFile" in part
            glb_path = os.path.join(tmpdir, part["glbFile"])
            assert os.path.exists(glb_path), "Missing: {}".format(glb_path)

        print("  [PASS] phase1+export: {} contacts, {} stages".format(
            len(contacts), len(stages)))
        print("         stages: {}".format(
            ", ".join(
                "S{}:{}".format(i + 1, ",".join(s))
                for i, s in enumerate(stages)
            )
        ))

        # Phase 2: Collision validation
        # Override bolt directions: bolts go +Y (away from base)
        directions["bolt_A"] = [0, 1, 0]
        directions["bolt_B"] = [0, 1, 0]
        directions["side_wall"] = [1, 0, 0]
        directions["base_plate"] = [0, 0, 1]

        validation = validate_disassembly_plan(parts, stages, directions, 300)
        report = generate_report(validation)

        print("\n" + report)

        assert validation["feasible_parts"] > 0, "At least some parts should be feasible"

        # Assembly JSON
        assembly = build_assembly_json(parts, stages, "test.stp", contacts, fasteners)
        json_path = os.path.join(tmpdir, "assembly.json")
        write_assembly_json(assembly, json_path)
        assert os.path.exists(json_path)

        with open(json_path) as f:
            data = json.load(f)
            assert data["stats"]["totalParts"] == len(parts)

        print("  [PASS] assembly_json: {} parts, {} stages".format(
            data["stats"]["totalParts"], data["stats"]["totalStages"]))


def test_pipeline_entry():
    """Test that pipeline.py can be imported and has expected structure."""
    import pipeline

    assert hasattr(pipeline, 'read_stp')
    assert hasattr(pipeline, 'brep_to_mesh')
    assert hasattr(pipeline, 'extract_assembly_tree')
    assert hasattr(pipeline, 'detect_contacts')
    assert hasattr(pipeline, 'identify_fasteners')
    assert hasattr(pipeline, 'build_disassembly_dag')
    assert hasattr(pipeline, 'compute_all_directions')
    assert hasattr(pipeline, 'build_assembly_json')
    assert hasattr(pipeline, 'check_disassembly_path')
    assert hasattr(pipeline, 'find_feasible_direction')
    assert hasattr(pipeline, 'validate_disassembly_plan')

    print("  [PASS] pipeline package: all 11 modules exported")


def main():
    print("=" * 60)
    print("Full Pipeline Validation Tests")
    print("=" * 60)

    try:
        test_pipeline_entry()
        test_full_pipeline()

        print("-" * 60)
        print("ALL PIPELINE TESTS PASSED")
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
