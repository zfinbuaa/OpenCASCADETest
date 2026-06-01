"""
glTF 2.0 binary export — merged mesh for optimal draw-call performance.

Each B-Rep shape is meshed via mesher.brep_to_mesh (all faces merged into
one vertex + index buffer) then written as a single-mesh-primitive .glb.
Draw calls drop from ~N_faces per part to exactly 1.
"""

import os
import re
import json
import struct
import logging
import numpy as np

logger = logging.getLogger(__name__)


_FILENAME_SAFE_RE = re.compile(r'[^\w\-.]', re.UNICODE)


def _sanitize_filename(name, fallback="part"):
    """Strict filename sanitizer: keep word chars, dash, dot; reject '..' segments."""
    if not name:
        return fallback
    s = _FILENAME_SAFE_RE.sub('_', str(name))
    # Forbid '..' and leading dots that could escape directories or hide files
    s = s.replace('..', '_')
    s = s.lstrip('.')
    if not s:
        return fallback
    # Cap length to avoid OS limits
    if len(s) > 120:
        s = s[:120]
    return s



def _compute_smooth_normals(vertices_arr, triangles_arr, face_normals_arr):
    vertex_normals = np.zeros_like(vertices_arr)
    np.add.at(vertex_normals, triangles_arr[:, 0], face_normals_arr)
    np.add.at(vertex_normals, triangles_arr[:, 1], face_normals_arr)
    np.add.at(vertex_normals, triangles_arr[:, 2], face_normals_arr)
    lengths = np.linalg.norm(vertex_normals, axis=1, keepdims=True)
    lengths = np.maximum(lengths, 1e-12)
    vertex_normals /= lengths
    return vertex_normals.astype(np.float32)


def _apply_transform(vertices_arr, transform):
    if transform is None:
        return vertices_arr.astype(np.float32)
    mat = np.array(transform, dtype=np.float64).reshape(4, 4, order='F')
    R = mat[:3, :3]
    t = mat[:3, 3]
    return (vertices_arr.astype(np.float64) @ R.T + t).astype(np.float32)


def _apply_normal_transform(normals_arr, transform):
    if transform is None:
        return normals_arr
    mat = np.array(transform, dtype=np.float64).reshape(4, 4, order='F')
    R = mat[:3, :3]
    try:
        # Singular check via determinant to avoid LinAlgError surprises
        det = np.linalg.det(R)
        if abs(det) < 1e-12:
            # Fall back to identity-like rotation
            normal_mat = np.eye(3, dtype=np.float64)
        else:
            normal_mat = np.linalg.inv(R).T
    except np.linalg.LinAlgError:
        normal_mat = np.eye(3, dtype=np.float64)
    transformed = (normals_arr.astype(np.float64) @ normal_mat).astype(np.float32)
    lengths = np.linalg.norm(transformed, axis=1, keepdims=True)
    lengths = np.maximum(lengths, 1e-12)
    transformed /= lengths
    return transformed


def _pad4(n):
    return (n + 3) & ~3


def _write_glb(positions, normals, indices, output_path, name="part"):
    max_index = int(indices.max())
    if max_index < 65536:
        index_dtype = np.uint16
        index_component_type = 5123
    else:
        index_dtype = np.uint32
        index_component_type = 5125

    indices_typed = indices.astype(index_dtype)
    positions_f32 = positions.astype(np.float32)
    normals_f32 = normals.astype(np.float32)

    positions_bytes = positions_f32.tobytes()
    normals_bytes = normals_f32.tobytes()
    indices_bytes = indices_typed.tobytes()

    pos_len = len(positions_bytes)
    nrm_len = len(normals_bytes)
    idx_len = len(indices_bytes)

    pos_padded = _pad4(pos_len)
    nrm_padded = _pad4(nrm_len)
    idx_padded = _pad4(idx_len)

    total_bin_len = pos_padded + nrm_padded + idx_padded

    bbox_min = positions_f32.min(axis=0).tolist()
    bbox_max = positions_f32.max(axis=0).tolist()
    normal_min = normals_f32.min(axis=0).tolist()
    normal_max = normals_f32.max(axis=0).tolist()

    gltf = {
        "asset": {"version": "2.0", "generator": "AutoModel"},
        "scene": 0,
        "scenes": [{"name": "Scene", "nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{
            "name": name,
            "primitives": [{
                "attributes": {"POSITION": 1, "NORMAL": 2},
                "indices": 0,
                "material": 0,
            }],
        }],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": index_component_type,
                "count": len(indices_typed),
                "type": "SCALAR",
                "max": [max_index],
                "min": [0],
            },
            {
                "bufferView": 1,
                "componentType": 5126,
                "count": len(positions),
                "type": "VEC3",
                "max": bbox_max,
                "min": bbox_min,
            },
            {
                "bufferView": 2,
                "componentType": 5126,
                "count": len(normals),
                "type": "VEC3",
                "max": normal_max,
                "min": normal_min,
            },
        ],
        "bufferViews": [
            {
                "buffer": 0,
                "byteOffset": pos_padded + nrm_padded,
                "byteLength": idx_len,
                "target": 34963,
            },
            {
                "buffer": 0,
                "byteOffset": 0,
                "byteLength": pos_len,
                "target": 34962,
            },
            {
                "buffer": 0,
                "byteOffset": pos_padded,
                "byteLength": nrm_len,
                "target": 34962,
            },
        ],
        "buffers": [{"byteLength": total_bin_len}],
        "materials": [{
            "pbrMetallicRoughness": {
                "baseColorFactor": [0.73, 0.73, 0.73, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.5,
            },
            "name": "default",
            "doubleSided": True,
        }],
    }

    json_str = json.dumps(gltf, separators=(',', ':'))
    json_bytes = json_str.encode('utf-8')
    json_padded_len = _pad4(len(json_bytes))

    total_size = 12 + 8 + json_padded_len + 8 + total_bin_len

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(struct.pack('<III', 0x46546C67, 2, total_size))
        f.write(struct.pack('<II', json_padded_len, 0x4E4F534A))
        f.write(json_bytes)
        f.write(b' ' * (json_padded_len - len(json_bytes)))
        f.write(struct.pack('<II', total_bin_len, 0x004E4942))
        f.write(positions_bytes)
        f.write(b'\x00' * (pos_padded - pos_len))
        f.write(normals_bytes)
        f.write(b'\x00' * (nrm_padded - nrm_len))
        f.write(indices_bytes)
        f.write(b'\x00' * (idx_padded - idx_len))

    return output_path


def export_merged_glb(shape, output_path, shape_name="part",
                      linear_deflection=1.0, angular_deflection=0.5,
                      transform=None):
    from pipeline.mesher import brep_to_mesh

    vertices, triangles, face_normals = brep_to_mesh(
        shape, linear_deflection, angular_deflection)

    if not triangles:
        return None

    vertices_arr = np.array(vertices, dtype=np.float32).reshape(-1, 3)
    triangles_arr = np.array(triangles, dtype=np.int64)
    face_normals_arr = np.array(face_normals, dtype=np.float32).reshape(-1, 3)

    vertex_normals = _compute_smooth_normals(
        vertices_arr, triangles_arr, face_normals_arr)

    vertices_arr = _apply_transform(vertices_arr, transform)
    vertex_normals = _apply_normal_transform(vertex_normals, transform)

    indices = triangles_arr.flatten().astype(np.uint32)

    return _write_glb(vertices_arr, vertex_normals, indices, output_path, shape_name)


def export_assembly_indexed(parts, output_dir, prefix="part",
                            linear_deflection=1.0, angular_deflection=0.5):
    os.makedirs(output_dir, exist_ok=True)
    result = []

    for i, part in enumerate(parts):
        shape = part.get("shape")
        raw_name = part.get("name", "part_{}".format(i))
        safe_name = _sanitize_filename(raw_name, fallback="part_{:04d}".format(i))
        glb_basename = "{}.glb".format(safe_name)
        glb_path = os.path.join(output_dir, glb_basename)

        if shape is not None:
            try:
                export_merged_glb(
                    shape, glb_path,
                    shape_name=part.get("name", "part_{}".format(i)),
                    linear_deflection=linear_deflection,
                    angular_deflection=angular_deflection)
            except Exception as e:
                logger.warning("glb export failed for %s: %s", safe_name, e)
                entry = dict(part)
                entry["glbFile"] = ""
                entry["index"] = i
                result.append(entry)
                continue

        entry = dict(part)
        entry["glbFile"] = "parts/" + glb_basename
        entry["index"] = i
        if shape is None:
            entry["glbFile"] = ""
        result.append(entry)

    return result
