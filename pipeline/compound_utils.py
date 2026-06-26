"""
Compound utilities — merge multiple leaf parts into a single compound unit.

When the user selects a set of leaf parts and groups them, this module:
  1. Merges B-Rep shapes into a single TopoDS_Compound (with transforms)
  2. Re-meshes the compound
  3. Exports a merged .glb file
  4. Builds MeshCollisionData for the compound
  5. Returns a new parts list with compound entries replacing grouped leaves
"""

import os
import sys
import numpy as np


def apply_compounds(parts, compounds, output_dir,
                    linear_deflection=1.0, angular_deflection=0.5):
    """
    Merge specified leaf parts into compound units.

    Args:
        parts: list of part dicts with 'name','shape','glbFile','color','transform'.
        compounds: [{"name": "Compound_A", "members": ["p1","p2"]}, ...]
        output_dir: dir to write merged .glb files.

    Returns:
        (new_parts, compound_info)
    """
    from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Builder
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCC.Core.gp import gp_Trsf

    from pipeline.gltf_exporter import _write_glb
    from pipeline.mesher import brep_to_mesh

    if not compounds:
        return parts, []

    part_map = {p["name"]: p for p in parts}
    members_to_compound = {}
    for c in compounds:
        for m in c.get("members", []):
            members_to_compound[m] = c["name"]

    compound_info = []
    compound_shapes = {}
    compound_glbs = {}
    compound_colors = {}

    for c in compounds:
        name = c["name"]
        members = [m for m in c.get("members", []) if m in part_map]
        if not members:
            continue

        sys.stdout.write("  Merging compound '{}' ({} parts)...\n".format(name, len(members)))
        sys.stdout.flush()

        builder = TopoDS_Builder()
        compound = TopoDS_Compound()
        builder.MakeCompound(compound)
        added = 0
        fallback_color = None

        for mname in members:
            p = part_map[mname]
            if p.get("shape") is None:
                continue
            shape = p["shape"]
            transform = p.get("transform")
            if transform and len(transform) == 16:
                try:
                    mat = np.array(transform, dtype=np.float64).reshape(4, 4, order='F')
                    trsf = gp_Trsf()
                    trsf.SetValues(
                        float(mat[0][0]), float(mat[1][0]), float(mat[2][0]), float(mat[3][0]),
                        float(mat[0][1]), float(mat[1][1]), float(mat[2][1]), float(mat[3][1]),
                        float(mat[0][2]), float(mat[1][2]), float(mat[2][2]), float(mat[3][2]),
                    )
                    shape = BRepBuilderAPI_Transform(shape, trsf, True).Shape()
                except Exception:
                    pass
            builder.Add(compound, shape)
            added += 1
            if p.get("color") and fallback_color is None:
                fallback_color = p["color"]

        if added == 0:
            continue

        vertices, triangles, normals = brep_to_mesh(compound, linear_deflection, angular_deflection)
        if vertices is None or len(vertices) == 0:
            sys.stdout.write("    WARNING: mesh failed for '{}'\n".format(name))
            continue

        parts_dir = os.path.join(output_dir, "parts")
        os.makedirs(parts_dir, exist_ok=True)
        glb_path = os.path.join(parts_dir, name.replace(" ", "_") + ".glb")

        verts_arr = np.array(vertices, dtype=np.float32).reshape(-1, 3)
        tri_arr = np.array(triangles, dtype=np.int64)
        nrm_arr = np.array(normals, dtype=np.float32).reshape(-1, 3)

        from pipeline.gltf_exporter import _compute_smooth_normals
        vtx_nrm = _compute_smooth_normals(verts_arr, tri_arr, nrm_arr)

        indices = tri_arr.flatten().astype(np.uint32)
        _write_glb(verts_arr, vtx_nrm, indices, glb_path, name=name)

        compound_shapes[name] = compound
        compound_glbs[name] = "parts/" + os.path.basename(glb_path)
        compound_colors[name] = fallback_color

        compound_info.append({
            "name": name,
            "members": members,
            "glbFile": "parts/" + os.path.basename(glb_path),
            "memberCount": len(members),
        })

    excluded = set(members_to_compound.keys())
    new_parts = [p for p in parts if p["name"] not in excluded]

    for name in compound_shapes:
        new_parts.append({
            "name": name,
            "shape": compound_shapes[name],
            "glbFile": compound_glbs[name],
            "color": compound_colors.get(name),
            "parent": "root",
            "transform": None,
            "isCompound": True,
            "compoundMembers": list(members_to_compound.keys()),
        })

    return new_parts, compound_info
