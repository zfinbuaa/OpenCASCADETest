"""
glTF 2.0 export using OCCT built-in functionality.

Exports B-Rep shapes and assembly trees to .glb files
for visualization in Three.js.
"""

import os
from OCC.Extend.DataExchange import write_gltf_file


def export_single_glb(shape, output_path, shape_name="part"):
    """
    Export a single B-Rep shape as a .glb file using OCCT's built-in exporter.

    Args:
        shape: TopoDS_Shape to export.
        output_path: File path for the .glb file.
        shape_name: Name label for the shape (reserved; OCCT write_gltf_file
                    does not currently support setting node name).

    Returns:
        str: The output file path.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    write_gltf_file(shape, output_path)
    return output_path


def export_assembly_to_glb(parts, output_dir):
    """
    Export an assembly's parts as individual .glb files.

    Args:
        parts: List of part dicts from flatten_assembly_tree().
               Each must have: name, shape.
        output_dir: Directory to write .glb files into.

    Returns:
        list[dict]: Parts list with 'glbFile' field added to each entry.
    """
    os.makedirs(output_dir, exist_ok=True)
    result = []

    for part in parts:
        safe_name = part["name"].replace("/", "_").replace("\\", "_").replace(" ", "_")
        glb_filename = "{}.glb".format(safe_name)
        glb_path = os.path.join(output_dir, glb_filename)

        export_single_glb(part["shape"], glb_path, part["name"])

        entry = dict(part)
        entry["glbFile"] = glb_filename
        result.append(entry)

    return result


def export_assembly_indexed(parts, output_dir, prefix="part"):
    """
    Export assembly parts with sequential index-based filenames.

    Args:
        parts: List of part dicts.
        output_dir: Output directory.
        prefix: Filename prefix (default "part").

    Returns:
        list[dict]: Parts list with 'glbFile' field added.
    """
    os.makedirs(output_dir, exist_ok=True)
    result = []

    for i, part in enumerate(parts):
        glb_basename = "{:s}_{:04d}.glb".format(prefix, i)
        glb_path = os.path.join(output_dir, glb_basename)

        export_single_glb(part["shape"], glb_path, part["name"])

        entry = dict(part)
        entry["glbFile"] = "parts/" + glb_basename
        entry["index"] = i
        result.append(entry)

    return result
