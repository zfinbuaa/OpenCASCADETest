"""
Verify whether OCCT can extract PMI leader-line geometry from STEP files.

Tests two approaches to access PMI presentation shapes:
  A) XCAFDimTolObjects_Tool (Dimension/Datum/GeomTolerance rich objects)
  B) TDF child labels of DimTol/Datum labels

Usage:
    python tests/test_pmi_geometry.py nist_ftc_09_asme1_ap242-e1.stp
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from collections import Counter
from OCC.Core.TDF import TDF_LabelSequence, TDF_ChildIterator
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_WIRE, TopAbs_VERTEX, TopAbs_FACE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.GeomAbs import GeomAbs_Line


def _extract_vertices_from_shape(shape):
    pts = []
    exp = TopExp_Explorer(shape, TopAbs_VERTEX)
    while exp.More():
        v = exp.Current()
        p = BRep_Tool.Pnt(v)
        pts.append(np.array([p.X(), p.Y(), p.Z()]))
        exp.Next()
    return pts


def _extract_line_endpoints(shape):
    endpoints = []
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        edge = exp.Current()
        try:
            adaptor = BRepAdaptor_Curve(edge)
            if adaptor.GetType() == GeomAbs_Line:
                p0 = adaptor.Value(adaptor.FirstParameter())
                p1 = adaptor.Value(adaptor.LastParameter())
                endpoints.append(
                    (np.array([p0.X(), p0.Y(), p0.Z()]),
                     np.array([p1.X(), p1.Y(), p1.Z()])))
        except Exception:
            pass
        exp.Next()
    return endpoints


def _shape_summary(shape):
    if shape is None or shape.IsNull():
        return "NULL"
    v = e = w = f = 0
    for t, c in [(TopAbs_VERTEX, 0), (TopAbs_EDGE, 1),
                  (TopAbs_WIRE, 2), (TopAbs_FACE, 3)]:
        exp = TopExp_Explorer(shape, t)
        while exp.More():
            if c == 0: v += 1
            elif c == 1: e += 1
            elif c == 2: w += 1
            else: f += 1
            exp.Next()
    return "V%d/E%d/W%d/F%d" % (v, e, w, f)


def main():
    if len(sys.argv) < 2:
        print("Usage: python tests/test_pmi_geometry.py <stp_file>")
        return 1

    stp_path = sys.argv[1]
    if not os.path.exists(stp_path):
        print("[FATAL] File not found: %s" % stp_path)
        return 1

    from pipeline.stp_reader import read_stp_with_doc
    from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool

    print("Reading: %s (%.1f KB)" % (stp_path, os.path.getsize(stp_path)/1024))
    doc = read_stp_with_doc(stp_path)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    dim_tol_tool = XCAFDoc_DocumentTool.DimTolTool(doc.Main())

    labels = TDF_LabelSequence()
    dim_tol_tool.GetDimTolLabels(labels)
    datum_labels = TDF_LabelSequence()
    dim_tol_tool.GetDatumLabels(datum_labels)
    print("DimTol labels: %d  |  Datum labels: %d" % (
        labels.Length(), datum_labels.Length()))

    # ============================================================
    # Method A: XCAFDimTolObjects_Tool (rich PMI objects)
    # ============================================================
    print("\n=== Method A: XCAFDimTolObjects_Tool ===")
    try:
        from OCC.Core.XCAFDimTolObjects import (
            XCAFDimTolObjects_Tool,
            XCAFDimTolObjects_DimensionObjectSequence,
            XCAFDimTolObjects_GeomToleranceObjectSequence,
            XCAFDimTolObjects_DatumObjectSequence,
        )
        dmt = XCAFDimTolObjects_Tool(doc)

        dim_seq = XCAFDimTolObjects_DimensionObjectSequence()
        dmt.GetDimensions(dim_seq)
        print("Dimensions: %d" % dim_seq.Length())
        for i in range(min(dim_seq.Length(), 3)):
            d = dim_seq.Value(i + 1)
            pres = d.GetPresentation()
            pts = _extract_vertices_from_shape(pres)
            lines = _extract_line_endpoints(pres)
            try:
                sname = str(d.GetSemanticName())
            except Exception:
                sname = "?"
            print("  #%d semantic='%s' %s  verts=%d  lines=%d" % (
                i + 1, sname, _shape_summary(pres), len(pts), len(lines)))
            if pts:
                print("    vert sample: %s" % pts[0])
            if lines:
                print("    line: %s -> %s" % (lines[0][0], lines[0][1]))

        tol_seq = XCAFDimTolObjects_GeomToleranceObjectSequence()
        datum_seq = XCAFDimTolObjects_DatumObjectSequence()
        dmt.GetGeomTolerances(tol_seq, datum_seq, None)
        print("\nGeomTolerances: %d  DatumObjects: %d" % (
            tol_seq.Length(), datum_seq.Length()))
        for i in range(min(datum_seq.Length(), 5)):
            d = datum_seq.Value(i + 1)
            pres = d.GetPresentation()
            pts = _extract_vertices_from_shape(pres)
            lines = _extract_line_endpoints(pres)
            try:
                dname = str(d.GetName())
            except Exception:
                dname = "?"
            print("  Datum #%d name='%s' %s  verts=%d  lines=%d" % (
                i + 1, dname, _shape_summary(pres), len(pts), len(lines)))
            if lines:
                print("    line[0]: %s -> %s" % (lines[0][0], lines[0][1]))

    except ImportError as e:
        print("XCAFDimTolObjects import failed: %s" % e)
    except Exception as e:
        print("Method A failed: %s" % e)

    # ============================================================
    # Method B: TDF child labels of DimTol/Datum labels
    # ============================================================
    print("\n=== Method B: TDF child labels of PMI labels ===")
    found_children = 0
    for source_name, seq in [("DimTol", labels), ("Datum", datum_labels)]:
        for i in range(seq.Length()):
            lab = seq.Value(i + 1)
            cit = TDF_ChildIterator(lab)
            has_child_shape = False
            while cit.More():
                child = cit.Value()
                cshape = shape_tool.GetShape(child)
                if cshape is not None and not cshape.IsNull():
                    s = _shape_summary(cshape)
                    pts = _extract_vertices_from_shape(cshape)
                    lines = _extract_line_endpoints(cshape)
                    if lines or pts:
                        has_child_shape = True
                        if found_children < 5:
                            print("  [%s #%d] child=%s verts=%d lines=%d" % (
                                source_name, i + 1, s, len(pts), len(lines)))
                            if lines:
                                print("    line: %s -> %s" % (
                                    lines[0][0], lines[0][1]))
                        found_children += 1
                        break
                cit.Next()
            if has_child_shape and found_children >= 5:
                break
    if found_children == 0:
        print("  No child labels with line/vertex geometry found.")
    else:
        print("  Total child labels with geometry: %d" % found_children)

    # ============================================================
    # Method C: Check if free shapes contain PMI-like wire mesh
    # ============================================================
    print("\n=== Method C: Free shape composition ===")
    all_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(all_shapes)
    print("Total free shapes: %d" % all_shapes.Length())
    comps = Counter()
    for i in range(min(all_shapes.Length(), 200)):
        s = shape_tool.GetShape(all_shapes.Value(i + 1))
        comps[_shape_summary(s)] += 1
    for k, v in comps.most_common(10):
        print("  %s: %d" % (k, v))

    print("\n" + "=" * 60)
    print("CONCLUSION: see above. If any approach found line endpoints,")
    print("  spatial matching (leader-line → part proximity) is viable.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
