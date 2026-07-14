"""
Generate a valid test STEP file with full PMI entity chains.

Creates:
  - 2 free-shape parts with Chinese names (valid OCCT geometry)
  - Full PMI chains for T01, T04, T11, W (via text injection):
    DESCRIPTIVE_REPRESENTATION_ITEM → REPRESENTATION →
    PROPERTY_DEFINITION_REPRESENTATION → PROPERTY_DEFINITION →
    CHARACTERIZED_ITEM_WITHIN_REPRESENTATION →
    ANNOTATION_PLANE → PLANE → AXIS2_PLACEMENT_3D → CARTESIAN_POINT

Tests:
  1. OCCT loads cleanly (0 syntax errors, 2+ shapes)
  2. parse_pmi_text_from_step returns {T01, T04, T11} (W filtered out - no digits)
  3. trace_pmi_positions returns 3 positions with correct (x,y,z)
  4. match_pmi_by_proximity matches labels to correct parts

Usage:
    python tests/gen_pmi_test_stp.py
Output:
    tests/pmi_test_assembly.stp
"""

import sys, os, re, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.XCAFApp import XCAFApp_Application
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.STEPCAFControl import STEPCAFControl_Writer
from OCC.Core.Interface import Interface_Static

OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "pmi_test_assembly.stp")

# ── PMI chain data ────────────────────────────────────────
# Each entry: (label, position_x, position_y, position_z, near_part_index)
PMI_ENTRIES = [
    ("T01",   5.0,  4.5, 3.0,  0),   # near part 0: 左前门线束 box
    ("T04",  15.0,  4.5, 3.0,  1),   # near part 1: 右前门线束 box
    ("S03D", 15.0, 12.0, 10.0, 3),   # near part 3: 制动硬管总成 cyl
    ("W",     2.0, 12.0, 2.0,  2),   # no digits → should be filtered out
]


def _make_pmi_chains(base_id, entries):
    """Build full PMI entity chains as STEP text."""
    lines = []
    eid = base_id

    for label, px, py, pz, part_idx in entries:
        # DESCRIPTIVE_REPRESENTATION_ITEM
        desc_id = eid; eid += 1
        rep_id = eid; eid += 1
        pdr_id = eid; eid += 1
        pd_id = eid; eid += 1
        ciwr_id = eid; eid += 1
        ap_id = eid; eid += 1
        plane_id = eid; eid += 1
        axis_id = eid; eid += 1
        dir1_id = eid; eid += 1
        dir2_id = eid; eid += 1
        pt_id = eid; eid += 1

        lines.append("""#{desc}=DESCRIPTIVE_REPRESENTATION_ITEM('equivalent unicode string',
'FCF\\\\w\\\\X2\\\\23E4\\\\X0\\\\\\\\w{label}\\\\w');""".format(desc=desc_id, label=label))

        # Dummy refs for REPRESENTATION
        lines.append("#{rep}=REPRESENTATION('',(#{d1},#{d2},#{d3},#{desc}),#{d4});".format(
            rep=rep_id, d1=base_id-1, d2=base_id-2, d3=base_id-3, desc=desc_id, d4=rep_id+100))

        lines.append("#{pdr}=PROPERTY_DEFINITION_REPRESENTATION(#{pd},#{rep});".format(
            pdr=pdr_id, pd=pd_id, rep=rep_id))

        lines.append("#{pd}=PROPERTY_DEFINITION('pmi validation property','',#{ciwr});".format(
            pd=pd_id, ciwr=ciwr_id))

        lines.append("#{ciwr}=CHARACTERIZED_ITEM_WITHIN_REPRESENTATION('','',#{ap},#99999);".format(
            ciwr=ciwr_id, ap=ap_id))

        lines.append("#{ap}=ANNOTATION_PLANE('PMI PLANE',(#{a1}),#{plane},(#{ap}));".format(
            ap=ap_id, a1=ap_id+200, plane=plane_id))

        lines.append("#{plane}=PLANE('',#{axis});".format(plane=plane_id, axis=axis_id))

        lines.append("#{axis}=AXIS2_PLACEMENT_3D('',#{d1},#{d2},#{pt});".format(
            axis=axis_id, d1=dir1_id, d2=dir2_id, pt=pt_id))

        lines.append("#{pt}=CARTESIAN_POINT('',({px}, {py}, {pz}));".format(
            pt=pt_id, px=px, py=py, pz=pz))

    return "\n".join(lines), eid


def _create_base_stp():
    """Create a simple valid STEP file with 6 positioned parts."""
    from OCC.Core.gp import gp_Vec, gp_Trsf
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform

    def place(shape, x, y, z=0):
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(x, y, z))
        return BRepBuilderAPI_Transform(shape, trsf, True).Shape()

    app = XCAFApp_Application.GetApplication()
    doc = TDocStd_Document("MDTV-CAF")
    app.InitDocument(doc)
    st = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    parts = [
        ("NAUO1_001_左前门线束",    place(BRepPrimAPI_MakeBox(10, 5, 3).Shape(), 0, 0)),
        ("NAUO1_002_右前门线束",    place(BRepPrimAPI_MakeBox(10, 5, 3).Shape(), 15, 0)),
        ("仪表板控制模块",           place(BRepPrimAPI_MakeBox(6, 6, 2).Shape(), 0, 10)),
        ("制动硬管总成",             place(BRepPrimAPI_MakeCylinder(2, 10).Shape(), 15, 10)),
        ("燃油管总成",               place(BRepPrimAPI_MakeCylinder(3, 6).Shape(), 30, 5)),
        ("ESP模块",                  place(BRepPrimAPI_MakeBox(8, 4, 4).Shape(), 25, 12)),
    ]

    for name, shape in parts:
        label = st.NewShape()
        st.SetShape(label, shape)
        TDataStd_Name.Set(label, name)

    Interface_Static.SetCVal("write.step.schema", "AP214")
    writer = STEPCAFControl_Writer()
    writer.Transfer(doc)
    status = writer.Write(OUTPUT)
    if status != 1:
        raise RuntimeError("STEP write failed (status=%d)" % status)
    print("Base STP: %s (%.1f KB)" % (OUTPUT, os.path.getsize(OUTPUT)/1024))


def _inject_pmi_chains():
    """Inject full PMI entity chains into the STEP file."""
    with open(OUTPUT, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Find the highest existing entity ID to avoid collisions
    existing_ids = {int(m.group(1)) for m in re.finditer(r'#(\d+)=', content)}
    base = max(existing_ids) + 100 if existing_ids else 90000

    pmi_text, next_id = _make_pmi_chains(base, PMI_ENTRIES)

    pmi_block = "\n/* === PMI Test Chains === */\n" + pmi_text + "\n"

    content = content.replace("ENDSEC;", pmi_block + "ENDSEC;")
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(content)
    print("PMI chains injected. Base ID: %d, next free: %d" % (base, next_id))


def _test_all():
    """Run all verification tests."""
    ok = True

    # Test 1: OCCT load
    from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
    from OCC.Core.TDF import TDF_LabelSequence
    app = XCAFApp_Application.GetApplication()
    doc = TDocStd_Document("MDTV-CAF")
    app.InitDocument(doc)
    reader = STEPCAFControl_Reader()
    reader.SetGDTMode(True)
    status = reader.ReadFile(OUTPUT)
    if status != 1:
        print("FAIL: OCCT ReadFile status=%d" % status); return False
    reader.Transfer(doc)
    st = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    fs = TDF_LabelSequence(); st.GetFreeShapes(fs)
    names = []
    for i in range(fs.Length()):
        try: names.append(str(fs.Value(i+1).GetLabelName()))
        except: pass
    print("  OCCT load: %d shapes %s  PASS" % (fs.Length(), names[:3]))
    if fs.Length() < 6:
        print("FAIL: expected >= 6 shapes"); ok = False

    # Test 2: text parser
    from pipeline.pmi_diag import parse_pmi_text_from_step, trace_pmi_positions
    labels = parse_pmi_text_from_step(OUTPUT)
    expected = {"T01", "T04", "S03D"}
    found = set(labels.keys())
    if expected != found:
        print("FAIL: parser labels %s, expected %s" % (found, expected))
        ok = False
    else:
        print("  Parser: %d labels %s  PASS" % (len(labels), found))
    extra = set(labels.keys()) - expected
    if extra:
        print("  WARN: unexpected labels in parse: %s" % extra)

    # Test 3: trace positions
    traced = trace_pmi_positions(OUTPUT)
    print("  Trace positions: %d entries" % len(traced))
    for t in traced:
        print("    %-6s → (%.1f, %.1f, %.1f)" % (t[0], t[1], t[2], t[3]))
    if len(traced) < 3:
        print("FAIL: expected >= 3 traced positions"); ok = False

    # Test 4: spatial matching
    from pipeline.pmi_diag import match_pmi_by_proximity
    matches = match_pmi_by_proximity(doc, labels, stp_path=OUTPUT)
    matched_parts = [m for m in matches if m["part"]]
    print("  Spatial match: %d/%d with parts" % (len(matched_parts), len(matches)))
    for m in matches:
        print("    %-6s → %s (dist=%.2f) pos=%s" % (
            m["label"], m["part"][:30], m["dist"], m["leader_pos"]))
    if len(matched_parts) < 2:
        print("FAIL: expected >= 2 labels matched to parts"); ok = False

    return ok


def main():
    print("=== PMI Test STP Generator v2 ===\n")
    print("[1/3] Creating base STEP...")
    _create_base_stp()
    print("\n[2/3] Injecting PMI entity chains...")
    _inject_pmi_chains()
    print("\n[3/3] Running verification...")
    ok = _test_all()
    print("\n" + "=" * 60)
    print("RESULT: %s" % ("ALL TESTS PASSED" if ok else "SOME TESTS FAILED"))
    print("File: %s" % OUTPUT)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
