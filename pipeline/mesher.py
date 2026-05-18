"""
B-Rep to triangle mesh conversion.

Converts OpenCASCADE B-Rep shapes to indexed triangle meshes
with per-vertex normals for use in glTF export and visualization.
"""

from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRep import BRep_Tool
from OCC.Core.TopLoc import TopLoc_Location


def brep_to_mesh(shape, linear_deflection=1.0, angular_deflection=0.5):
    """
    Convert a B-Rep Shape to triangle mesh data.

    Args:
        shape: TopoDS_Shape to tessellate.
        linear_deflection: Maximum chordal deviation in mm.
        angular_deflection: Maximum angular deviation in radians.

    Returns:
        tuple: (vertices, triangles, normals)
            vertices: flat list [x0,y0,z0, x1,y1,z1, ...]
            triangles: list of [i0,i1,i2] index triplets
            normals: flat list [nx,ny,nz, ...] per triangle (NOT per vertex)
    """
    mesh = BRepMesh_IncrementalMesh(shape, linear_deflection, False, angular_deflection)
    mesh.Perform()

    vertices = []
    triangles = []
    normals = []

    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = exp.Current()
        loc = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation(face, loc)

        if triangulation is None:
            exp.Next()
            continue

        nb_nodes = triangulation.NbNodes()
        offset = len(vertices) // 3  # vertex index offset
        transform = loc.Transformation()

        for i in range(1, nb_nodes + 1):
            p = triangulation.Node(i)
            p.Transform(transform)
            vertices.extend([p.X(), p.Y(), p.Z()])

        nb_tris = triangulation.NbTriangles()
        for i in range(1, nb_tris + 1):
            tri = triangulation.Triangle(i)
            t1, t2, t3 = tri.Value(1), tri.Value(2), tri.Value(3)
            idx0 = offset + t1 - 1
            idx1 = offset + t2 - 1
            idx2 = offset + t3 - 1
            triangles.append([idx0, idx1, idx2])

            v0_x = vertices[idx0 * 3]
            v0_y = vertices[idx0 * 3 + 1]
            v0_z = vertices[idx0 * 3 + 2]
            v1_x = vertices[idx1 * 3]
            v1_y = vertices[idx1 * 3 + 1]
            v1_z = vertices[idx1 * 3 + 2]
            v2_x = vertices[idx2 * 3]
            v2_y = vertices[idx2 * 3 + 1]
            v2_z = vertices[idx2 * 3 + 2]

            ux = v1_x - v0_x
            uy = v1_y - v0_y
            uz = v1_z - v0_z
            wx = v2_x - v0_x
            wy = v2_y - v0_y
            wz = v2_z - v0_z

            nx = uy * wz - uz * wy
            ny = uz * wx - ux * wz
            nz = ux * wy - uy * wx
            length = (nx * nx + ny * ny + nz * nz) ** 0.5
            if length > 1e-12:
                normals.extend([nx / length, ny / length, nz / length])
            else:
                normals.extend([0.0, 0.0, 1.0])

        exp.Next()

    return vertices, triangles, normals


def get_mesh_stats(vertices, triangles, normals):
    """Return summary statistics for a mesh."""
    from numpy import array

    v = array(vertices).reshape(-1, 3)
    bbox_min = v.min(axis=0).tolist()
    bbox_max = v.max(axis=0).tolist()
    center = v.mean(axis=0).tolist()

    return {
        "vertex_count": len(v),
        "triangle_count": len(triangles),
        "normal_count": len(normals) // 3,
        "bbox_min": bbox_min,
        "bbox_max": bbox_max,
        "center": center,
    }
