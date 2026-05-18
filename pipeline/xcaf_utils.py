"""
XCAF document utilities - assembly tree extraction and traversal.

Extracts the full assembly tree from an OCCT XCAF document, including
part names, colors, positions, and hierarchical relationships.
"""

import numpy as np
from OCC.Core.XCAFDoc import (
    XCAFDoc_DocumentTool,
    XCAFDoc_ShapeTool,
)
from OCC.Core.TDF import TDF_LabelSequence
from OCC.Core.TDataStd import TDataStd_Name

try:
    from OCC.Core.XCAFDoc import XCAFDoc_ColorSurf
except ImportError:
    from OCC.Core.XCAFDoc import XCAFDoc_ColorGen as XCAFDoc_ColorSurf


def get_shape_name(label, shape_tool):
    """Get the name of a shape label. Uses TDataStd_Name for 7.8 compatibility."""
    try:
        name_attr = TDataStd_Name()
        if label.FindAttribute(TDataStd_Name.GetID(), name_attr):
            dump_output = name_attr.Dump()
            if isinstance(dump_output, tuple) and len(dump_output) >= 2:
                s = str(dump_output[1])
                if "Name=|" in s:
                    start = s.index("Name=|") + 6
                    end = s.index("|", start)
                    return s[start:end]
    except Exception:
        pass
    return "Part_{}".format(label.Tag())


def set_shape_name(label, name):
    """Set name on a TDF label using TDataStd_Name (compatible with 7.8)."""
    TDataStd_Name.Set(label, name)


def loc_to_matrix(loc):
    """
    Convert a TopLoc_Location to a 4x4 column-major transform matrix.

    Returns:
        list[float]: 16-element column-major 4x4 matrix (glTF convention),
                     or None if the location is identity.
    """
    if loc is None or loc.IsIdentity():
        return None

    trsf = loc.Transformation()
    mat = np.eye(4, dtype=np.float64)

    mat[0, 0] = trsf.Value(1, 1)
    mat[0, 1] = trsf.Value(1, 2)
    mat[0, 2] = trsf.Value(1, 3)
    mat[0, 3] = trsf.Value(1, 4)
    mat[1, 0] = trsf.Value(2, 1)
    mat[1, 1] = trsf.Value(2, 2)
    mat[1, 2] = trsf.Value(2, 3)
    mat[1, 3] = trsf.Value(2, 4)
    mat[2, 0] = trsf.Value(3, 1)
    mat[2, 1] = trsf.Value(3, 2)
    mat[2, 2] = trsf.Value(3, 3)
    mat[2, 3] = trsf.Value(3, 4)

    return mat.T.flatten().tolist()


def extract_assembly_tree(doc):
    """
    Extract the full assembly tree from an XCAF document.

    Each node contains:
        - label: OCAF TDF_Label
        - name: part name string
        - shape: TopoDS_Shape (None for assembly-only nodes)
        - children: list of child nodes
        - color: [r, g, b] or None
        - transform: 4x4 column-major matrix list[16] or None

    Returns:
        list[dict]: List of root assembly nodes.
    """
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool(doc.Main())

    def traverse(label, parent_loc=None, depth=0):
        node = {
            "label": label,
            "name": get_shape_name(label, shape_tool),
            "shape": shape_tool.GetShape(label) if shape_tool.IsFree(label) else None,
            "children": [],
            "color": None,
            "transform": None,
            "depth": depth,
        }

        if color_tool.IsSet(label, XCAFDoc_ColorSurf):
            c = color_tool.GetColor(label, XCAFDoc_ColorSurf)
            node["color"] = [c.Red(), c.Green(), c.Blue()]

        if shape_tool.IsAssembly(label):
            child_seq = TDF_LabelSequence()
            shape_tool.GetComponents(label, child_seq)
            for i in range(child_seq.Length()):
                child_label = child_seq.Value(i + 1)
                child_loc = shape_tool.GetLocation(child_label)
                child = traverse(child_label, child_loc, depth + 1)
                child["transform"] = loc_to_matrix(child_loc)
                node["children"].append(child)

        return node

    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)

    roots = []
    for i in range(free_shapes.Length()):
        roots.append(traverse(free_shapes.Value(i + 1)))

    return roots


def flatten_assembly_tree(roots):
    """
    Flatten an assembly tree into a linear list of leaf parts.

    Only leaf nodes with a shape and no children are included.
    Compound/assembly nodes with children are skipped (their shape
    already contains the sum of children geometry).

    Returns:
        list[dict]: Flat list of leaf part dictionaries.
    """
    parts = []

    def traverse(node, parent_name=None):
        has_children = len(node.get("children", [])) > 0

        if node.get("shape") is not None and not has_children:
            part = {
                "name": node["name"],
                "shape": node["shape"],
                "color": node.get("color"),
                "transform": node.get("transform"),
                "parent": parent_name,
            }
            parts.append(part)

        for child in node.get("children", []):
            traverse(child, node["name"])

    for root in roots:
        traverse(root)

    return parts


def get_tree_stats(roots):
    """
    Return statistics about the assembly tree.

    Returns:
        dict: Summary of node counts by type.
    """
    total_nodes = 0
    shape_nodes = 0
    assembly_nodes = 0

    def traverse(node):
        nonlocal total_nodes, shape_nodes, assembly_nodes
        total_nodes += 1
        if node.get("shape") is not None:
            shape_nodes += 1
        if node.get("children"):
            assembly_nodes += 1
        for child in node.get("children", []):
            traverse(child)

    for root in roots:
        traverse(root)

    return {
        "total_nodes": total_nodes,
        "shape_nodes": shape_nodes,
        "assembly_nodes": assembly_nodes,
    }
