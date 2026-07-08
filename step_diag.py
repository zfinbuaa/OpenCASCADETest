"""
STEP Assembly Tree Diagnostic Tool
===================================
Diagnoses assembly tree extraction for a STEP (.stp) file.
Outputs per-node info: name, IsAssembly, ShapeType, children count, etc.

Usage:
    python step_diag.py <stp_file> [--output report.json] [--max-parts N]

Dependencies: pythonocc-core only.
"""

import sys
import os
import json
import argparse
from collections import OrderedDict

try:
    from OCC.Core.TDocStd import TDocStd_Document
    from OCC.Core.XCAFApp import XCAFApp_Application
    from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
    from OCC.Core.XCAFDoc import (
        XCAFDoc_DocumentTool,
        XCAFDoc_ShapeTool,
    )
    from OCC.Core.TDF import TDF_LabelSequence, TDF_ChildIterator
    from OCC.Core.TDataStd import TDataStd_Name
    from OCC.Core.TopAbs import TopAbs_COMPOUND, TopAbs_SOLID, TopAbs_SHELL
    from OCC.Core.TopExp import TopExp_Explorer
except ImportError as e:
    print("[FATAL] pythonocc-core not found: %s" % e)
    print("Install: pip install pythonocc-core")
    sys.exit(1)


TOPABS_NAMES = {
    0: "COMPOUND",
    1: "COMPSOLID",
    2: "SOLID",
    3: "SHELL",
    4: "FACE",
    5: "WIRE",
    6: "EDGE",
    7: "VERTEX",
    8: "SHAPE",
}


def _shape_type_name(shape_enum_val):
    """Get human-readable shape type name."""
    try:
        return TOPABS_NAMES.get(int(shape_enum_val), "TYPE_%d" % int(shape_enum_val))
    except Exception:
        return "NONE"


class DiagNode(OrderedDict):
    def __init__(self):
        super().__init__()
        self["entry"] = ""
        self["name"] = ""
        self["has_name_attr"] = False
        self["is_assembly"] = False
        self["is_component"] = False
        self["is_free"] = False
        self["is_shape"] = False
        self["shape_type"] = "NONE"
        self["depth"] = 0
        self["component_count"] = 0
        self["sub_shape_count"] = 0
        self["solid_count"] = 0
        self["parent_entry"] = ""
        self["children"] = []


def read_stp_diag(filepath):
    """Read a STEP file into an XCAF document for diagnosis."""
    app = XCAFApp_Application.GetApplication()
    doc = TDocStd_Document("MDTV-CAF")
    app.InitDocument(doc)

    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    reader.SetLayerMode(True)
    reader.SetPropsMode(True)
    reader.SetGDTMode(True)
    reader.SetMatMode(True)
    reader.SetViewMode(True)

    status = reader.ReadFile(filepath)
    if status != 1:
        raise IOError("STEPCAFControl_Reader.ReadFile failed, status=%d" % status)

    if not reader.Transfer(doc):
        raise RuntimeError("STEPCAFControl_Reader.Transfer failed")

    return doc


def _get_label_name(label):
    """Get label name via GetLabelName (preserves Unicode)."""
    try:
        name = label.GetLabelName()
        if name and name.strip():
            return name
    except Exception:
        pass
    return "Part_%d" % label.Tag()


def _get_entry(label):
    """Get label entry path as string like '0:1:2:3'."""
    try:
        return str(label.EntryDump())
    except Exception:
        return "?"


def _has_name_attr(label):
    """Check if label has TDataStd_Name attribute."""
    try:
        guid = TDataStd_Name.GetID()
        return label.IsAttribute(guid)
    except Exception:
        return False


def traverse(label, shape_tool, parent_entry="", depth=0, max_nodes=None, counter=None):
    """Recursively traverse a label and build diagnostic info."""
    if counter is None:
        counter = {"count": 0}

    if max_nodes is not None and counter["count"] >= max_nodes:
        return None

    counter["count"] += 1

    node = DiagNode()
    entry = _get_entry(label)
    node["entry"] = entry
    node["parent_entry"] = parent_entry
    node["depth"] = depth

    # -- Name --
    node["name"] = _get_label_name(label)
    node["has_name_attr"] = _has_name_attr(label)

    # -- Assembly / Component status --
    try:
        node["is_assembly"] = shape_tool.IsAssembly(label)
    except Exception:
        node["is_assembly"] = None

    try:
        node["is_component"] = shape_tool.IsComponent(label)
    except Exception:
        node["is_component"] = None

    try:
        node["is_free"] = shape_tool.IsFree(label)
    except Exception:
        node["is_free"] = None

    try:
        node["is_shape"] = shape_tool.IsShape(label)
    except Exception:
        node["is_shape"] = None

    # -- Shape type --
    try:
        shape = shape_tool.GetShape(label)
        if shape is not None and not shape.IsNull():
            st = shape.ShapeType()
            node["shape_type"] = _shape_type_name(st)
            # Count solids inside
            sol_count = 0
            exp = TopExp_Explorer(shape, TopAbs_SOLID)
            while exp.More():
                sol_count += 1
                exp.Next()
            node["solid_count"] = sol_count
        else:
            node["shape_type"] = "NONE"
            node["solid_count"] = 0
    except Exception:
        node["shape_type"] = "ERROR"
        node["solid_count"] = 0

    # -- Child components --
    if node["is_assembly"]:
        try:
            child_seq = TDF_LabelSequence()
            shape_tool.GetComponents(label, child_seq)
            node["component_count"] = child_seq.Length()
        except Exception:
            node["component_count"] = -1
    else:
        # Check if there are sub-shapes even without IsAssembly
        try:
            sub_seq = TDF_LabelSequence()
            shape_tool.GetSubShapes(label, sub_seq)
            node["sub_shape_count"] = sub_seq.Length()
        except Exception:
            node["sub_shape_count"] = -1

        try:
            child_seq = TDF_LabelSequence()
            shape_tool.GetComponents(label, child_seq)
            node["component_count"] = child_seq.Length()
        except Exception:
            node["component_count"] = -1

    # -- Recurse children --
    if node["is_assembly"] or node["component_count"] > 0 or node["sub_shape_count"] > 0:
        try:
            child_seq = TDF_LabelSequence()
            if node["is_assembly"] or node["component_count"] > 0:
                shape_tool.GetComponents(label, child_seq)
            else:
                shape_tool.GetSubShapes(label, child_seq)

            for i in range(child_seq.Length()):
                child_label = child_seq.Value(i + 1)
                child_node = traverse(
                    child_label, shape_tool,
                    parent_entry=entry,
                    depth=depth + 1,
                    max_nodes=max_nodes,
                    counter=counter,
                )
                if child_node is not None:
                    node["children"].append(child_node)
        except Exception:
            pass

    return node


def diagnose_stp(filepath, max_nodes=None):
    """
    Diagnose a STEP file and return the assembly tree with full metadata.

    Returns dict with:
        filepath, root_count, roots (list of DiagNode trees),
        summary (counts by type).
    """
    print("Reading STEP: %s" % filepath)
    doc = read_stp_diag(filepath)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)
    root_count = free_shapes.Length()
    print("Root shapes: %d" % root_count)

    # Collect stats
    counter = {"count": 0}

    roots = []
    for i in range(root_count):
        label = free_shapes.Value(i + 1)
        root_node = traverse(
            label, shape_tool,
            parent_entry="",
            depth=0,
            max_nodes=max_nodes,
            counter=counter,
        )
        if root_node is not None:
            roots.append(root_node)

    print("Total nodes traversed: %d" % counter["count"])

    # -- Summary --
    def _count_by(node, key):
        result = {}
        def _walk(n):
            v = n.get(key)
            if v is True:
                v = "true"
            elif v is False:
                v = "false"
            result[str(v)] = result.get(str(v), 0) + 1
            for c in n.get("children", []):
                _walk(c)
        _walk(node)
        return result

    summary = {
        "total_nodes": counter["count"],
        "root_count": root_count,
        "shape_types": _count_by(
            {"children": roots}, "shape_type"
        ) if roots else {},
        "is_assembly": _count_by(
            {"children": roots}, "is_assembly"
        ) if roots else {},
        "is_component": _count_by(
            {"children": roots}, "is_component"
        ) if roots else {},
        "is_free": _count_by(
            {"children": roots}, "is_free"
        ) if roots else {},
    }

    # Find nodes that might be misclassified:
    #   shape_type == COMPOUND but is_assembly == False
    #   These are the ones that would get _S001 split
    misclassified = []

    def _find_misclassified(node, path):
        st = node.get("shape_type", "")
        is_ass = node.get("is_assembly", False)
        comp_count = node.get("component_count", 0)
        name = node.get("name", "")
        entry = node.get("entry", "")

        if st == "COMPOUND" and not is_ass and comp_count == 0:
            misclassified.append({
                "entry": entry,
                "name": name,
                "depth": node.get("depth", 0),
                "solid_count": node.get("solid_count", 0),
                "sub_shape_count": node.get("sub_shape_count", 0),
                "path": " / ".join(path + [name]),
            })

        for c in node.get("children", []):
            _find_misclassified(c, path + [name])

    for root in roots:
        _find_misclassified(root, [])

    summary["misclassified_compounds"] = len(misclassified)
    summary["misclassified_details"] = misclassified[:50]

    return {
        "filepath": filepath,
        "root_count": root_count,
        "roots": roots,
        "summary": summary,
    }


def main():
    parser = argparse.ArgumentParser(
        description="STEP Assembly Tree Diagnostic Tool"
    )
    parser.add_argument("stp_file", help="Path to .stp / .step file")
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output JSON file path (default: print to stdout)"
    )
    parser.add_argument(
        "--summary-only", action="store_true",
        help="Only output summary, skip full tree"
    )
    parser.add_argument(
        "--max-nodes", type=int, default=20000,
        help="Maximum nodes to traverse (default: 20000)"
    )
    args = parser.parse_args()

    stp_file = args.stp_file
    if not os.path.exists(stp_file):
        print("[FATAL] File not found: %s" % stp_file)
        return 1

    try:
        result = diagnose_stp(stp_file, max_nodes=args.max_nodes)
    except Exception as e:
        print("[FATAL] %s: %s" % (type(e).__name__, e))
        import traceback
        traceback.print_exc()
        return 2

    # -- Output --
    out_data = OrderedDict()
    out_data["filepath"] = result["filepath"]
    out_data["summary"] = result["summary"]

    if not args.summary_only:
        out_data["roots"] = result["roots"]

    json_text = json.dumps(
        out_data,
        ensure_ascii=False,
        indent=2,
        default=str,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_text)
        print("Report written: %s (%.1f KB)" % (
            args.output, os.path.getsize(args.output) / 1024))
    else:
        # Print summary + misclassified first, then optionally full JSON
        print()
        print("=" * 60)
        print("Summary")
        print("=" * 60)
        for k, v in result["summary"].items():
            if k != "misclassified_details":
                print("  %s: %s" % (k, v))
        print()
        mc_list = result["summary"].get("misclassified_details", [])
        if mc_list:
            print("Misclassified COMPOUND nodes (would become _S001 splits):")
            for m in mc_list[:20]:
                print("  [%s] %s  (solids=%d, sub_shapes=%d)" % (
                    m["entry"], m["name"], m["solid_count"], m["sub_shape_count"]))
            if len(mc_list) > 20:
                print("  ... and %d more" % (len(mc_list) - 20))
        else:
            print("No misclassified COMPOUND nodes found.")
        print()
        print("Full JSON: length %d chars. Use --output to save to file." % len(json_text))

    return 0


if __name__ == "__main__":
    sys.exit(main())
