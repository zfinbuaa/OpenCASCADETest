"""
XCAF document utilities - assembly tree extraction and traversal.

Extracts the full assembly tree from an OCCT XCAF document, including
part names, colors, positions, hierarchical relationships, and
sub-assembly structure.

Supports multi-level AP214 assemblies with NEXT_ASSEMBLY_USAGE_OCCURRENCE,
PRODUCT_DEFINITION, and TRANSFORMATION data.
"""

import numpy as np
from OCC.Core.XCAFDoc import (
    XCAFDoc_DocumentTool,
    XCAFDoc_ShapeTool,
)
from OCC.Core.TDF import TDF_LabelSequence
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.TopAbs import TopAbs_COMPOUND, TopAbs_SOLID
from OCC.Core.TopExp import TopExp_Explorer

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


def _compute_shape_centroid(shape):
    """Compute centroid of a TopoDS_Shape."""
    try:
        props = GProp_GProps()
        brepgprop.VolumeProperties(shape, props)
        if props.Mass() > 1e-12:
            c = props.CentreOfMass()
            return np.array([c.X(), c.Y(), c.Z()])
        props2 = GProp_GProps()
        brepgprop.SurfaceProperties(shape, props2)
        c = props2.CentreOfMass()
        return np.array([c.X(), c.Y(), c.Z()])
    except Exception:
        return None


def extract_assembly_tree(doc):
    """
    Extract the full assembly tree from an XCAF document.

    Each node contains:
        - label: OCAF TDF_Label
        - name: part name string
        - shape: TopoDS_Shape (leaf nodes only, None for assembly-only nodes)
        - children: list of child nodes
        - color: [r, g, b] or None
        - transform: 4x4 column-major matrix list[16] or None
        - is_leaf: bool (True for leaf parts with no children)
        - child_names: list[str] (direct child names)
        - ancestor_path: list[str] (full path from root)
        - depth: int

    Returns:
        list[dict]: List of root assembly nodes.
    """
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool(doc.Main())

    def traverse(label, parent_loc=None, depth=0, ancestor_path=None):
        if ancestor_path is None:
            ancestor_path = []

        name = get_shape_name(label, shape_tool)
        current_path = ancestor_path + [name]

        has_children = shape_tool.IsAssembly(label)
        shape = shape_tool.GetShape(label)

        is_compound = (not has_children and shape is not None
                       and shape.ShapeType() == TopAbs_COMPOUND)

        if has_children:
            is_leaf = False
        elif is_compound:
            is_leaf = False
        else:
            is_leaf = True

        node = {
            "label": label,
            "name": name,
            "shape": shape if is_leaf else None,
            "children": [],
            "color": None,
            "transform": None,
            "depth": depth,
            "is_leaf": is_leaf,
            "child_names": [],
            "ancestor_path": current_path,
        }

        if color_tool.IsSet(label, XCAFDoc_ColorSurf):
            c = color_tool.GetColor(label, XCAFDoc_ColorSurf)
            node["color"] = [c.Red(), c.Green(), c.Blue()]

        if has_children:
            child_seq = TDF_LabelSequence()
            shape_tool.GetComponents(label, child_seq)
            for i in range(child_seq.Length()):
                child_label = child_seq.Value(i + 1)
                child_loc = shape_tool.GetLocation(child_label)
                child = traverse(child_label, child_loc, depth + 1, current_path)
                child["transform"] = loc_to_matrix(child_loc)
                node["children"].append(child)
                node["child_names"].append(child["name"])
        elif is_compound:
            solid_idx = 0
            exp = TopExp_Explorer(shape, TopAbs_SOLID)
            while exp.More():
                solid = exp.Current()
                solid_name = "{}_S{:03d}".format(name, solid_idx + 1)
                child = {
                    "label": None,
                    "name": solid_name,
                    "shape": solid,
                    "children": [],
                    "color": node.get("color"),
                    "transform": None,
                    "depth": depth + 1,
                    "is_leaf": True,
                    "child_names": [],
                    "ancestor_path": current_path + [solid_name],
                }
                node["children"].append(child)
                node["child_names"].append(solid_name)
                solid_idx += 1
                exp.Next()

        return node

    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)

    roots = []
    for i in range(free_shapes.Length()):
        root_label = free_shapes.Value(i + 1)
        root = traverse(root_label)
        root["transform"] = loc_to_matrix(shape_tool.GetLocation(root_label))
        roots.append(root)

    return roots


def _multiply_transforms(parent_col, child_col):
    """Multiply two column-major 4x4 transforms: parent @ child."""
    if parent_col is None:
        return child_col
    if child_col is None:
        return parent_col
    p = np.array(parent_col, dtype=np.float64).reshape(4, 4, order='F')
    c = np.array(child_col, dtype=np.float64).reshape(4, 4, order='F')
    return (p @ c).flatten(order='F').tolist()


def flatten_assembly_tree(roots):
    """
    Flatten an assembly tree into leaf parts and sub-assembly nodes.

    Leaf parts receive the accumulated world-space transform (product of
    all ancestor local-to-parent matrices) so GLB export places them
    correctly in world coordinates.

    Returns:
        tuple: (leaf_parts, sub_assemblies)
            leaf_parts: list[dict] with keys:
                name, shape, color, transform, parent, ancestors
            sub_assemblies: list[dict] with keys:
                name, child_names, depth, centroid, ancestor_path
    """
    leaf_parts = []
    sub_assemblies = []

    def traverse(node, parent_name=None, ancestor_path=None,
                 parent_transform=None):
        if ancestor_path is None:
            ancestor_path = []

        name = node["name"]
        current_path = ancestor_path + [name]

        world_transform = _multiply_transforms(
            parent_transform, node.get("transform"))

        if node.get("is_leaf", False):
            direct_parent = parent_name if parent_name else name
            part = {
                "name": name,
                "shape": node.get("shape"),
                "color": node.get("color"),
                "transform": world_transform,
                "parent": direct_parent,
                "ancestors": current_path,
            }
            leaf_parts.append(part)
        else:
            centroid = None
            if node.get("shape") is not None:
                centroid = _compute_shape_centroid(node["shape"])

            sa = {
                "name": name,
                "child_names": node.get("child_names", []),
                "depth": node.get("depth", 0),
                "centroid": centroid,
                "ancestor_path": current_path,
            }
            sub_assemblies.append(sa)

        for child in node.get("children", []):
            traverse(child, name, current_path, world_transform)

    for root in roots:
        traverse(root)

    if leaf_parts and not sub_assemblies:
        sub_assemblies.append({
            "name": leaf_parts[0].get("parent", "root"),
            "child_names": [p["name"] for p in leaf_parts],
            "depth": 0,
            "centroid": None,
            "ancestor_path": [leaf_parts[0].get("parent", "root")],
        })

    return leaf_parts, sub_assemblies


def filter_parts_by_ancestor(parts, root_name):
    """Keep only leaf parts whose ancestor path includes root_name.

    Used to generate disassembly plans scoped to a specific sub-assembly
    level selected by the user.
    """
    if not root_name:
        return parts
    return [p for p in parts if root_name in p.get("ancestors", [])]


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
