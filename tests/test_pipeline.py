"""
End-to-end pipeline validation test.

Tests the full OCCT pipeline using programmatically generated
geometry (no external STP file required).
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_brep_to_mesh():
    """Test B-Rep to triangle mesh conversion with a simple box."""
    from pipeline.mesher import brep_to_mesh, get_mesh_stats
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

    box = BRepPrimAPI_MakeBox(10, 5, 3).Shape()
    verts, tris, norms = brep_to_mesh(box, linear_deflection=0.5)

    assert len(verts) > 0, "No vertices generated"
    assert len(tris) > 0, "No triangles generated"
    assert len(norms) > 0, "No normals generated"
    assert len(verts) % 3 == 0, "Vertices not flat XYZ triplets"
    assert len(norms) % 3 == 0, "Normals not flat XYZ triplets"
    assert len(norms) // 3 == len(tris), "Normals count != triangles count"

    stats = get_mesh_stats(verts, tris, norms)
    assert stats["vertex_count"] == len(verts) // 3
    assert stats["triangle_count"] == len(tris)

    print("  [PASS] brep_to_mesh: {} vertices, {} triangles".format(
        stats["vertex_count"], stats["triangle_count"]))


def test_brep_to_mesh_complex():
    """Test mesh conversion with a compound shape (fused box + cylinder)."""
    from pipeline.mesher import brep_to_mesh
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse

    box = BRepPrimAPI_MakeBox(10, 5, 3).Shape()
    cyl = BRepPrimAPI_MakeCylinder(2, 8).Shape()
    assy = BRepAlgoAPI_Fuse(box, cyl).Shape()

    verts, tris, norms = brep_to_mesh(assy, linear_deflection=0.5)

    assert len(verts) >= 24, "Too few vertices for compound shape"
    assert len(tris) >= 12, "Too few triangles for compound shape"
    assert len(norms) // 3 == len(tris), "Normals != triangles"

    print("  [PASS] brep_to_mesh (compound): {} vertices, {} triangles".format(
        len(verts) // 3, len(tris)))


def test_gltf_export():
    """Test glTF export using OCCT built-in exporter."""
    from pipeline.gltf_exporter import export_single_glb
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

    box = BRepPrimAPI_MakeBox(10, 5, 3).Shape()

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "test.glb")
        result = export_single_glb(box, out_path, "test_box")

        assert os.path.exists(result), "glb file not created"
        file_size = os.path.getsize(result)
        assert file_size > 0, "glb file is empty"

        print("  [PASS] gltf_export: {:d} bytes written".format(file_size))


def test_xcaf_assembly_tree():
    """Test XCAF assembly tree extraction with nested assembly."""
    from pipeline.xcaf_utils import extract_assembly_tree, flatten_assembly_tree, get_tree_stats
    from OCC.Core.TDocStd import TDocStd_Document
    from OCC.Core.XCAFApp import XCAFApp_Application
    from OCC.Core.XCAFDoc import (
        XCAFDoc_DocumentTool, XCAFDoc_ShapeTool,
    )
    from OCC.Core.TDF import TDF_Label
    from OCC.Core.TopLoc import TopLoc_Location
    from OCC.Core.TDataStd import TDataStd_Name
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder

    # Create XCAF document
    app = XCAFApp_Application.GetApplication()
    doc = TDocStd_Document("test")
    app.InitDocument(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    box = BRepPrimAPI_MakeBox(10, 5, 3).Shape()
    cyl = BRepPrimAPI_MakeCylinder(2, 8).Shape()

    root_label = shape_tool.NewShape()
    box_label = shape_tool.AddComponent(root_label, box, False)
    TDataStd_Name.Set(box_label, "PartA_Box")

    sub_label = shape_tool.NewShape()
    TDataStd_Name.Set(sub_label, "SubAssembly")
    shape_tool.AddComponent(root_label, sub_label, TopLoc_Location())
    cyl_label = shape_tool.AddComponent(sub_label, cyl, False)
    TDataStd_Name.Set(cyl_label, "PartB_Cylinder")

    # Extract tree
    roots = extract_assembly_tree(doc)
    assert len(roots) >= 1, "No root nodes extracted"

    stats = get_tree_stats(roots)
    print("  [PASS] xcaf_assembly_tree: {} nodes, {} shape nodes, {} assemblies".format(
        stats["total_nodes"], stats["shape_nodes"], stats["assembly_nodes"]))

    # Flatten
    parts = flatten_assembly_tree(roots)
    assert len(parts) >= 1, "No parts after flatten"
    print("  [PASS] flatten_assembly_tree: {} parts".format(len(parts)))


def test_full_pipeline():
    """Test the complete STP import-less pipeline with synthetic data."""
    from pipeline.mesher import brep_to_mesh
    from pipeline.gltf_exporter import export_single_glb
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox

    box = BRepPrimAPI_MakeBox(10, 5, 3).Shape()

    verts, tris, norms = brep_to_mesh(box, linear_deflection=0.5)

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "pipeline_test.glb")
        export_single_glb(box, out_path, "test")

        assert os.path.exists(out_path), "Pipeline output not created"
        assert os.path.getsize(out_path) > 100, "Pipeline output too small"

        print("  [PASS] full_pipeline: B-Rep -> Mesh -> glb ({} bytes)".format(
            os.path.getsize(out_path)))


def main():
    print("=" * 60)
    print("Pipeline Validation Tests")
    print("=" * 60)

    try:
        test_brep_to_mesh()
        test_brep_to_mesh_complex()
        test_gltf_export()
        test_xcaf_assembly_tree()
        test_full_pipeline()

        print("-" * 60)
        print("ALL TESTS PASSED")
        return 0

    except ImportError as e:
        print("\n[SKIP] OCCT not installed: {}".format(e))
        print("Install with: conda install -c conda-forge pythonocc-core=7.9.3")
        return 1

    except Exception as e:
        print("\n[FAIL] {}: {}".format(type(e).__name__, e))
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
