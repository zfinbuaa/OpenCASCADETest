# 整车三维数模自动拆装方案生成系统 — 技术实施路线

> 基于 OpenCASCADE (OCCT) + Three.js + Electron  
> 输入格式: STEP (STP) — 统一 B-Rep 精确几何管线  
> 版本 2.0 | 2026-05

---

## 一、系统架构

```
┌─────────────────────────────────────────────────────────┐
│                 OCCT 几何处理服务 (Python)                │
│                                                          │
│  STEP (.stp)                                             │
│       │                                                  │
│  ┌────▼─────────────────────────────────────────────┐   │
│  │ 1. 格式解析     XCAF 文档 → 装配树 + B-Rep        │   │
│  │ 2. 几何离散     BRepMesh → 三角网格 (+法线)       │   │
│  │ 3. 装配分析     面接触检测 / 紧固件识别            │   │
│  │ 4. 拆装解算     碰撞检测 + 拓扑 DAG + 路径规划     │   │
│  │ 5. 格式输出     glTF 2.0 (.glb) + assembly.json   │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
└───────────────────────┬─────────────────────────────────┘
                        │  parts/*.glb + assembly.json
                        ▼
┌─────────────────────────────────────────────────────────┐
│             Three.js 前端 (Electron 桌面应用)             │
│                                                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐ │
│  │ 位置图    │ │ 爆炸图    │ │ 标注     │ │ 相机采图   │ │
│  │ (已实现)  │ │ (已实现)  │ │ (已实现)  │ │ (新增)    │ │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘ │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

## 二、阶段 0：基础设施搭建（3-4 人天）

### 2.1 OCCT 环境

```bash
# 推荐 conda 安装，免编译
conda create --name=pyoccenv python=3.12
conda activate pyoccenv
conda install -c conda-forge pythonocc-core=7.9.3
# 辅助库
pip install pygltflib numpy trimesh
```

```python
# 验证环境
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
box = BRepPrimAPI_MakeBox(1,1,1).Shape()
print("OCCT OK, shape vertices:", box.NbVertices())
```

### 2.2 STP 文件读取

STP 格式自带完整 B-Rep 几何 + 装配层级 + 颜色信息，通过 XCAF 文档一次性导入：

```python
from OCC.Extend.DataExchange import read_step_file

# 读取 STP，返回 TopoDS_Shape 列表（每个根节点一个 Shape）
shapes = read_step_file("assembly.stp")
print(f"加载 {len(shapes)} 个根形状")

# 转换为 XCAF 文档以获取完整的装配树+颜色+名称
from OCC.Extend.DataExchange import read_step_file_with_names_colors
doc = read_step_file_with_names_colors("assembly.stp")
```

### 2.3 B-Rep → 三角网格（含法线）

```python
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRep import BRep_Tool
from OCC.Core.TopLoc import TopLoc_Location

def brep_to_mesh(shape, linear_deflection=1.0, angular_deflection=0.5):
    """
    将 B-Rep Shape 三角剖分，返回 (vertices, triangles, normals)
    linear_deflection: 线性偏差 (mm)，越小越精细
    angular_deflection: 角度偏差 (弧度)
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
        
        if triangulation is not None:
            # 顶点
            nb_nodes = triangulation.NbNodes()
            offset = len(vertices) // 3  # vertex index offset
            transform = loc.Transformation()
            for i in range(1, nb_nodes + 1):
                p = triangulation.Node(i)  # OCCT 7.8: Node(), 7.9+ adds Nodes()
                p.Transform(transform)
                vertices.extend([p.X(), p.Y(), p.Z()])
            
            # 三角面片 (OCCT 使用 1-based 索引)
            nb_tris = triangulation.NbTriangles()
            for i in range(1, nb_tris + 1):
                t = triangulation.Triangle(i)  # OCCT 7.8: Triangle(), 7.9+ adds Triangles()
                t1, t2, t3 = t.Value(1), t.Value(2), t.Value(3)
                idx0 = offset + t1 - 1
                idx1 = offset + t2 - 1
                idx2 = offset + t3 - 1
                triangles.append([idx0, idx1, idx2])
                
                # 计算面法线 (使用叉积)
                v0 = [vertices[idx0*3+j] for j in range(3)]
                v1 = [vertices[idx1*3+j] for j in range(3)]
                v2 = [vertices[idx2*3+j] for j in range(3)]
                u = [v1[0]-v0[0], v1[1]-v0[1], v1[2]-v0[2]]
                w = [v2[0]-v0[0], v2[1]-v0[1], v2[2]-v0[2]]
                nx = u[1]*w[2] - u[2]*w[1]
                ny = u[2]*w[0] - u[0]*w[2]
                nz = u[0]*w[1] - u[1]*w[0]
                length = (nx*nx + ny*ny + nz*nz) ** 0.5
                if length > 1e-12:
                    normals.extend([nx/length, ny/length, nz/length])
                else:
                    normals.extend([0, 0, 1])
        exp.Next()
    
    return vertices, triangles, normals
```

### 2.4 glTF 2.0 Binary (.glb) 输出

OCCT 7.8+ 内置 glTF 导出，无需手写编码器。支持 PBR 材质、多 Mesh 节点层级、颜色纹理等完整 glTF 2.0 特性。

```python
from OCC.Extend.DataExchange import write_gltf_file

def export_part_as_glb(shape, output_path, node_name="part"):
    """
    将 B-Rep Shape 三角剖分并导出为 .glb 文件。
    使用 OCCT 内置 glTF export，自动处理网格剖分和序列化。
    """
    write_gltf_file(shape, output_path)
    return output_path
```

**高级用法** — 将整个装配体分批导出为多个 glb（每个零件一个文件）：

```python
def export_assembly_to_glb(parts_tree, output_dir):
    """
    遍历装配树，每个叶子零件导出独立的 .glb 文件。
    parts_tree: 由 extract_assembly_tree() 返回的装配树
    """
    import os
    os.makedirs(output_dir, exist_ok=True)
    
    result = []
    
    def traverse(node, parent_name=""):
        if node.get("shape") is not None:
            part_id = node["name"].replace(" ", "_")
            glb_path = os.path.join(output_dir, f"{part_id}.glb")
            export_part_as_glb(node["shape"], glb_path, node["name"])
            node["glbFile"] = os.path.basename(glb_path)
            result.append(node)
        
        for child in node.get("children", []):
            traverse(child, node["name"])
    
    for root in parts_tree:
        traverse(root)
    
    return result
```

### 2.5 验证

```python
# test_pipeline.py — 跑通全链路
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Fuse

# 创建简单装配体
box = BRepPrimAPI_MakeBox(10, 1, 5).Shape()
cyl = BRepPrimAPI_MakeCylinder(1, 3).Shape()
assy = BRepAlgoAPI_Fuse(box, cyl).Shape()

verts, indices, normals = brep_to_mesh(assy, linear_deflection=0.5)
print(f"Mesh: {len(verts)//3} vertices, {len(indices)//3} triangles, {len(normals)//3} normals")

# 内置 glTF 导出
from OCC.Extend.DataExchange import write_gltf_file
write_gltf_file(assy, "test.glb")
print("glTF export: test.glb")
```

---

## 三、阶段 1：装配分析与拆装顺序生成（15-20 人天）

### 3.1 装配树遍历

```python
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
from OCC.Core.TDF import TDF_LabelSequence
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.gp import gp_Trsf
import numpy as np

def extract_assembly_tree(doc):
    """
    从 XCAF 文档提取装配树。
    返回: { label, name, shape, children[], color, transform }
    
    transform 格式为 4x4 变换矩阵 list[16] (列主序，与 glTF 一致)，
    或 None 表示单位矩阵。
    """
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool(doc.Main())
    
    def loc_to_matrix(loc):
        """将 TopLoc_Location 转换为 4x4 列主序矩阵 list[16]"""
        if loc is None or loc.IsIdentity():
            return None
        trsf = loc.Transformation()
        mat = np.eye(4, dtype=np.float64)
        mat[0, 0] = trsf.Value(1, 1); mat[0, 1] = trsf.Value(1, 2)
        mat[0, 2] = trsf.Value(1, 3); mat[0, 3] = trsf.Value(1, 4)
        mat[1, 0] = trsf.Value(2, 1); mat[1, 1] = trsf.Value(2, 2)
        mat[1, 2] = trsf.Value(2, 3); mat[1, 3] = trsf.Value(2, 4)
        mat[2, 0] = trsf.Value(3, 1); mat[2, 1] = trsf.Value(3, 2)
        mat[2, 2] = trsf.Value(3, 3); mat[2, 3] = trsf.Value(3, 4)
        return mat.T.flatten().tolist()  # 列主序
    
    def traverse(label, parent_loc=None, depth=0):
        node = {
            "label": label,
            "name": shape_tool.GetName(label) or f"Part_{label.Tag()}",
            "shape": shape_tool.GetShape(label) if shape_tool.IsFree(label) else None,
            "children": [],
            "color": None,
            "transform": None,
        }
        
        # 提取颜色
        if color_tool.IsSet(label, XCAFDoc_ColorSurf):
            c = color_tool.GetColor(label, XCAFDoc_ColorSurf)
            node["color"] = [c.Red(), c.Green(), c.Blue()]
        
        # 遍历子节点
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
    
    # 从根自由形状开始
    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)
    roots = []
    for i in range(free_shapes.Length()):
        roots.append(traverse(free_shapes.Value(i + 1)))
    
    return roots
```

### 3.2 装配约束识别（面-面接触）

```python
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
from OCC.Core.gp import gp_Pnt, gp_Dir
import numpy as np

CONTACT_THRESHOLD = 0.1  # mm，接触判定阈值

def detect_contacts(parts):
    """
    检测所有零件两两之间的接触关系。
    返回: [ { partA, partB, contactPoints[], avgNormal } ]
    """
    contacts = []
    
    for i, part_a in enumerate(parts):
        for j, part_b in enumerate(parts):
            if j <= i:
                continue
            
            dist_calc = BRepExtrema_DistShapeShape(
                part_a["shape"], part_b["shape"]
            )
            dist_calc.Perform()
            
            if not dist_calc.IsDone():
                continue
            
            min_dist = dist_calc.Value()
            if min_dist > CONTACT_THRESHOLD:
                continue
            
            # 收集接触点
            contact_points = []
            normals = []
            for k in range(1, dist_calc.NbSolution() + 1):
                p1 = dist_calc.PointOnShape1(k)
                p2 = dist_calc.PointOnShape2(k)
                if p1.Distance(p2) < CONTACT_THRESHOLD:
                    contact_points.append((p1.X(), p1.Y(), p1.Z()))
                    # 计算法线方向 (从 B 指向 A)
                    dx = p1.X() - p2.X()
                    dy = p1.Y() - p2.Y()
                    dz = p1.Z() - p2.Z()
                    length = (dx*dx + dy*dy + dz*dz) ** 0.5
                    if length > 1e-10:
                        normals.append((dx/length, dy/length, dz/length))
            
            if contact_points:
                avg_normal = np.mean(normals, axis=0).tolist() if normals else [0, 0, 1]
                contacts.append({
                    "partA": part_a["name"],
                    "partB": part_b["name"],
                    "contactPoints": contact_points,
                    "avgNormal": avg_normal
                })
    
    return contacts
```

### 3.3 紧固件识别（基于几何特征）

```python
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop, brepgprop_Surface

def identify_fasteners(parts, contacts, volume_ratio_threshold=0.05):
    """
    识别紧固件（螺栓/螺母/垫圈）。
    特征：
    - 体积明显小于相邻件（< 总体积的 5%）
    - 至少与 2 个其他零件有面接触
    """
    total_volume = 0
    volumes = {}
    
    for part in parts:
        props = GProp_GProps()
        brepgprop.VolumePropertiespart["shape"], props)
        vol = props.Mass()
        volumes[part["name"]] = vol
        total_volume += vol
    
    # 统计每个零件的接触面数量
    contact_count = {}
    for c in contacts:
        contact_count[c["partA"]] = contact_count.get(c["partA"], 0) + 1
        contact_count[c["partB"]] = contact_count.get(c["partB"], 0) + 1
    
    fasteners = []
    for part in parts:
        name = part["name"]
        if (volumes.get(name, 0) < total_volume * volume_ratio_threshold 
            and contact_count.get(name, 0) >= 2):
            fasteners.append(name)
    
    return fasteners
```

### 3.4 拆卸顺序 DAG 生成

```python
from collections import deque

def build_disassembly_dag(parts, contacts, fasteners):
    """
    构建拆卸有向无环图 (DAG)。
    
    规则：
    1. 紧固件优先拆除（stage 1）
    2. 移除紧固件的接触关系后，重新计算入度
    3. 入度为 0 的节点可拆除（BFS 拓扑排序）
    4. 每层 BFS 对应一个拆卸阶段
    """
    part_names = {p["name"] for p in parts}
    
    # 构建邻接表（被阻挡关系）
    blocked_by = {name: set() for name in part_names}
    blocks = {name: set() for name in part_names}
    
    for c in contacts:
        a, b = c["partA"], c["partB"]
        # 根据法线方向判断阻挡关系
        # 简化：双向阻挡
        blocked_by[a].add(b)
        blocked_by[b].add(a)
        blocks[a].add(b)
        blocks[b].add(a)
    
    # 阶段 1: 紧固件（入度为 0 的紧固件可拆除）
    removed = set()
    stages = []
    
    # 第一遍：识别紧固件
    fastener_set = set(fasteners)
    stage1 = [f for f in fastener_set if f in part_names]
    if stage1:
        stages.append(stage1)
        removed.update(stage1)
    
    # BFS 拓扑排序
    remaining = part_names - removed
    while remaining:
        # 计算当前入度 = 剩余零件中还有多少与之接触
        current_stage = []
        for name in list(remaining):
            effective_blockers = blocked_by[name] & remaining
            if len(effective_blockers) == 0:
                current_stage.append(name)
        
        if not current_stage:
            # 死锁：取入度最小的零件作为下一阶段
            min_blocker = min(remaining, key=lambda n: len(blocked_by[n] & remaining))
            current_stage = [min_blocker]
        
        stages.append(current_stage)
        removed.update(current_stage)
        remaining = part_names - removed
    
    return stages
```

### 3.5 拆卸方向计算

```python
def calc_disassembly_direction(part_name, contacts, parts):
    """
    计算零件的拆卸方向 = 所有接触面法线的平均方向（取反）
    若无接触面，使用零件包围盒主成分方向
    """
    normals = []
    for c in contacts:
        if c["partA"] == part_name:
            normals.append([-n for n in c["avgNormal"]])  # 取反
        elif c["partB"] == part_name:
            normals.append(c["avgNormal"])
    
    if normals:
        avg = np.mean(normals, axis=0)
        length = np.linalg.norm(avg)
        if length > 1e-10:
            return (avg / length).tolist()
    
    # 回退：用包围盒长轴方向
    return [0, 1, 0]  # 默认 +Y
```

### 3.6 assembly.json 输出格式

```json
{
  "name": "车门总成",
  "sourceFile": "door_assembly.jt",
  "parts": [
    {
      "id": "outer_panel",
      "name": "外板",
      "glbFile": "parts/outer_panel.glb",
      "volume_mm3": 125000,
      "isFastener": false,
      "disassemblyStage": 2,
      "direction": { "x": 0, "y": 1, "z": 0 },
      "distanceMultiplier": 1.0,
      "color": [0.8, 0.2, 0.2]
    }
  ],
  "groups": [
    {
      "id": "door_assy",
      "name": "车门总成",
      "members": ["outer_panel", "inner_panel", "window_glass"],
      "stage": 2
    }
  ],
  "stages": [
    { "stage": 1, "description": "拆除紧固件", "parts": ["bolt_01","bolt_02","bolt_03"] },
    { "stage": 2, "description": "拆除外覆盖件", "parts": ["outer_panel","window_glass"] },
    { "stage": 3, "description": "拆除内部结构", "parts": ["inner_panel","hinge"] }
  ]
}
```

---

## 四、阶段 2：碰撞检测验证拆卸路径（10-15 人天）

### 4.1 扫掠碰撞检测

```python
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.gp import gp_Trsf, gp_Vec
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop

def check_disassembly_path(part_shape, other_shapes, direction, 
                           max_distance=500, steps=20):
    """
    沿拆卸方向逐步移动零件，检查与其余零件的干涉。
    使用 布尔求交(Cut) + 体积比较 判定碰撞。
    
    返回: {
        feasible: bool,
        max_safe_distance: float,  # 最大安全移动距离 (mm)
        collision_at_step: int,     # 发生碰撞的步数 (-1 表示无碰撞)
        collision_with: str or None # 碰撞的零件名
    }
    """
    step_size = max_distance / steps
    
    for step in range(1, steps + 1):
        # 沿拆卸方向移动零件
        vec = gp_Vec(
            direction[0] * step * step_size,
            direction[1] * step * step_size,
            direction[2] * step * step_size
        )
        transform = gp_Trsf()
        transform.SetTranslation(vec)
        moved_shape = BRepBuilderAPI_Transform(part_shape, transform).Shape()
        
        # 计算移动后零件的体积
        props_moved = GProp_GProps()
        brepgprop.VolumePropertiesmoved_shape, props_moved)
        vol_moved = props_moved.Mass()
        
        # 检查与每个其他零件的干涉
        for other_name, other_shape in other_shapes:
            cut = BRepAlgoAPI_Cut(moved_shape, other_shape)
            if not cut.IsDone():
                continue
            
            # 求交结果：如果 Cut 产物体积比原 shape 小 → 有干涉
            # （Cut = moved - other，如果两者不重叠，Cut 结果体积 = vol_moved）
            cut_shape = cut.Shape()
            props_cut = GProp_GProps()
            brepgprop.VolumePropertiescut_shape, props_cut)
            vol_cut = props_cut.Mass()
            
            # 体积减少超过 0.1% → 判定为碰撞
            if vol_moved > 1e-6 and (vol_moved - vol_cut) / vol_moved > 0.001:
                safe_dist = (step - 1) * step_size
                return {
                    "feasible": False,
                    "max_safe_distance": safe_dist,
                    "collision_at_step": step,
                    "collision_with": other_name
                }
    
    return {
        "feasible": True,
        "max_safe_distance": max_distance,
        "collision_at_step": -1,
        "collision_with": None
    }
```

### 4.2 多方向尝试策略

```python
def find_feasible_direction(part_shape, others, preferred_dir, max_distance):
    """
    尝试多个方向寻找可行的拆卸路径。
    
    尝试顺序：
    1. 优选方向（接触面法线方向）
    2. 6 个主轴方向 (±X, ±Y, ±Z)
    3. 随机采样方向（RRT 简化）
    """
    candidates = []
    
    # 1. 优选方向
    candidates.append(preferred_dir)
    
    # 2. 主轴方向
    axes = [
        [1,0,0], [-1,0,0], [0,1,0], [0,-1,0], [0,0,1], [0,0,-1]
    ]
    for axis in axes:
        if axis != preferred_dir:
            candidates.append(axis)
    
    # 3. 对角线方向 (8 个)
    import itertools
    for signs in itertools.product([-1,1], repeat=3):
        d = list(signs)
        length = sum(x*x for x in d) ** 0.5
        candidates.append([x/length for x in d])
    
    for direction in candidates:
        result = check_disassembly_path(
            part_shape, others, direction, max_distance
        )
        if result["feasible"] and result["max_safe_distance"] > max_distance * 0.5:
            return direction, result
    
    return None  # 无可行方向，需人工干预
```

---

## 五、阶段 3：前端功能对接（10-15 人天）

### 5.1 assembly.json 消费

```javascript
// js/assembly-loader.js (新增文件)

import { ModelLoader } from './model-loader.js';

export class AssemblyLoader {
  /**
   * 加载 assembly.json 并批量加载所有 glb 文件。
   * 返回填充好的 assemblyGroups[] 供爆炸引擎使用。
   */
  static async loadAssembly(jsonPath) {
    // 1. 读取 assembly.json
    const response = await fetch(jsonPath);
    const assembly = await response.json();
    const baseDir = jsonPath.substring(0, jsonPath.lastIndexOf('/'));

    // 2. 批量加载 glb
    const loadedParts = new Map();
    for (const part of assembly.parts) {
      const glbPath = baseDir + '/' + part.glbFile;
      const data = await ModelLoader.loadModel(glbPath);
      loadedParts.set(part.id, {
        ...part,
        modelData: data,
        meshes: ModelLoader.getAllMeshes(data.root),
      });
    }

    // 3. 转换为 AssemblyGroup[] (直接对接 explosion-view.js)
    const groups = [];
    for (const group of assembly.groups || []) {
      const meshes = [];
      for (const memberId of group.members) {
        const part = loadedParts.get(memberId);
        if (part) meshes.push(...part.meshes);
      }
      groups.push({
        id: group.id,
        name: group.name,
        meshes: meshes.flat(),
        direction: assembly.parts.find(p => p.id === group.members[0])?.direction || '+Y',
        distanceMultiplier: 1.0,
        stage: group.stage,
      });
    }

    // 4. 没有显式编组的零件，各自成组
    const groupedIds = new Set((assembly.groups || []).flatMap(g => g.members));
    for (const part of assembly.parts) {
      if (!groupedIds.has(part.id)) {
        const data = loadedParts.get(part.id);
        if (data) {
          groups.push({
            id: part.id,
            name: part.name,
            meshes: data.meshes,
            direction: part.direction || '+Y',
            distanceMultiplier: part.distanceMultiplier || 1.0,
            stage: part.disassemblyStage || 1,
          });
        }
      }
    }

    return { assembly, loadedParts, groups };
  }
}
```

### 5.2 爆炸图对接

```javascript
// explosion-view.js 新增方法

/**
 * 从 assembly.json 的编组数据直接填充爆炸编组。
 * 替代手动"从层级自动创建编组"。
 */
loadAssemblyGroups(groups) {
  this._clearGroups();
  this.assemblyGroups = groups;
  
  // 注册 mesh → group 映射
  for (const g of groups) {
    for (const mesh of g.meshes) {
      this.meshToGroup.set(mesh, g.id);
    }
  }
  
  this._renderGroupList();
  this._setStatus(`已加载 ${groups.length} 个装配编组，可点击"分组爆炸"`);
}
```

### 5.3 逐阶段爆炸动画

```javascript
// explosion-view.js 新增方法

/**
 * 逐阶段动画爆炸。
 * 每个拆卸阶段间隔 800ms，带动画过渡。
 */
async explodeGroupsAnimated(duration = 600) {
  if (this.assemblyGroups.length === 0) {
    this._setStatus('请先加载编组');
    return;
  }

  this.resetPositions();

  // 按阶段分组
  const stageMap = new Map();
  for (const g of this.assemblyGroups) {
    const s = g.stage || 1;
    if (!stageMap.has(s)) stageMap.set(s, []);
    stageMap.get(s).push(g);
  }

  const stages = Array.from(stageMap.keys()).sort((a, b) => a - b);

  for (const stage of stages) {
    this._setStatus(`拆卸阶段 ${stage} / ${stages.length}`);
    const groups = stageMap.get(stage);
    
    // 计算目标位置
    const targets = new Map(); // mesh → Vector3
    for (const group of groups) {
      const dir = this._directionToVector(group.direction);
      const dist = this.explosionDistance * group.distanceMultiplier;
      // ... 计算每个 mesh 的目标位置
    }

    // 动画过渡
    await this._animateToPositions(targets, duration);
    
    // 阶段间暂停
    await new Promise(r => setTimeout(r, 500));
  }

  this.isExploded = true;
  this._setStatus('拆卸动画完成');
}

_animateToPositions(targets, duration) {
  return new Promise((resolve) => {
    const startTime = performance.now();
    const startPositions = new Map();
    
    for (const [mesh, target] of targets) {
      startPositions.set(mesh, this.explodedPositions.get(mesh).clone());
    }

    const animate = () => {
      const elapsed = performance.now() - startTime;
      const t = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic

      for (const [mesh, target] of targets) {
        const start = startPositions.get(mesh);
        const current = new THREE.Vector3().lerpVectors(start, target, eased);
        this.explodedPositions.set(mesh, current.clone());
        this._moveMeshTo(mesh, current);
      }

      if (t < 1) {
        requestAnimationFrame(animate);
      } else {
        resolve();
      }
    };

    requestAnimationFrame(animate);
  });
}
```

### 5.4 相机自动采图

```javascript
// js/camera-capture.js (新增文件)

export class CameraCapture {
  constructor(sceneManager, exportManager) {
    this.scene = sceneManager;
    this.export = exportManager;
  }

  /**
   * 为拆卸阶段批量截图。
   * @param {number} numStages - 总拆卸阶段数
   * @returns {Promise<Object[]>} 截图数据数组
   */
  async captureAllStages(numStages) {
    const captures = [];
    
    for (let stage = 1; stage <= numStages; stage++) {
      // 每个阶段取 3 个视角
      const views = [
        this._viewDisassemblyDirection(stage),
        this._viewIsometric(stage),
        this._viewTopDown(stage),
      ];
      
      for (const { position, target, label } of views) {
        this.scene.camera.position.copy(position);
        this.scene.camera.lookAt(target);
        this.scene.controls.target.copy(target);
        this.scene.controls.update();
        
        // 等待渲染完成
        await new Promise(r => requestAnimationFrame(r));
        
        const dataUrl = this.scene.renderer.domElement.toDataURL('image/png');
        captures.push({ stage, label, dataUrl });
      }
    }
    
    return captures;
  }

  _viewDisassemblyDirection(stage) {
    return {
      position: new THREE.Vector3(10, 3, 0),
      target: new THREE.Vector3(0, 0, 0),
      label: `stage_${stage}_side`
    };
  }

  _viewIsometric(stage) {
    const d = 8;
    return {
      position: new THREE.Vector3(d, d * 0.7, d),
      target: new THREE.Vector3(0, 0, 0),
      label: `stage_${stage}_iso`
    };
  }

  _viewTopDown(stage) {
    return {
      position: new THREE.Vector3(0, 12, 0.01),
      target: new THREE.Vector3(0, 0, 0),
      label: `stage_${stage}_top`
    };
  }
}
```

---

## 六、阶段 4：集成与验证（5-8 人天）

### 6.1 管线串联脚本

```python
# pipeline.py — 一键处理入口

#!/usr/bin/env python3
"""
整车数模自动拆装方案生成管线

用法:
  python pipeline.py input.stp --output-dir ./output/
  
产出:
  output/
  ├── parts/*.glb        # 每个零件一个 glb 文件
  ├── assembly.json       # 装配结构与拆装方案
  └── report.txt          # 处理报告
"""

import argparse, os, json
from OCC.Extend.DataExchange import read_step_file_with_names_colors
from OCC.Extend.DataExchange import write_gltf_file

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', help='STEP (.stp) 输入文件')
    parser.add_argument('--output-dir', default='./output')
    parser.add_argument('--mesh-deflection', type=float, default=1.0,
                       help='三角剖分线性偏差 (mm)')
    args = parser.parse_args()
    
    os.makedirs(args.output_dir, exist_ok=True)
    os.makedirs(os.path.join(args.output_dir, 'parts'), exist_ok=True)
    
    # Step 1: 读取 STP 模型
    print("[1/6] 读取 STP 模型...")
    doc = read_step_file_with_names_colors(args.input)
    
    # Step 2: 提取装配树
    print("[2/6] 提取装配树...")
    parts = extract_assembly_tree(doc)
    print(f"  发现 {len(parts)} 个根节点")
    
    # 展平零件列表
    all_parts = []
    def flatten(node, parent=None):
        if node.get("shape") is not None:
            node["parent"] = parent
            all_parts.append(node)
        for child in node.get("children", []):
            flatten(child, node["name"])
    for root in parts:
        flatten(root)
    print(f"  展平后 {len(all_parts)} 个零件")
    
    # Step 3: 三角剖分 + 导出 glb (使用 OCCT 内置)
    print("[3/6] 三角剖分 + 导出 glb...")
    for i, part in enumerate(all_parts):
        glb_path = os.path.join(args.output_dir, 'parts', f'{i:04d}.glb')
        write_gltf_file(part["shape"], glb_path)
        part["glbFile"] = f'parts/{i:04d}.glb'
        print(f"  [{i+1}/{len(all_parts)}] {part['name']} -> {glb_path}")
    
    # Step 4: 装配分析
    print("[4/6] 装配约束分析...")
    contacts = detect_contacts(all_parts)
    fasteners = identify_fasteners(all_parts, contacts)
    print(f"  {len(contacts)} 个接触关系, {len(fasteners)} 个紧固件")
    
    # Step 5: 拆装解算
    print("[5/6] 拆卸顺序解算...")
    stages = build_disassembly_dag(all_parts, contacts, fasteners)
    for part in all_parts:
        part["direction"] = calc_disassembly_direction(part["name"], contacts, all_parts)
    print(f"  {len(stages)} 个拆卸阶段")
    
    # Step 6: 输出
    print("[6/6] 输出 assembly.json...")
    output_json = build_output_json(all_parts, stages, args.input)
    with open(os.path.join(args.output_dir, 'assembly.json'), 'w') as f:
        json.dump(output_json, f, indent=2, ensure_ascii=False)
    
    print(f"\n完成! 输出目录: {args.output_dir}")

if __name__ == '__main__':
    main()
```

### 6.2 Electron 集成

```javascript
// main.js 新增菜单项

{
  label: '导入 STP 数模',
  click: async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: '选择 STEP 数模文件',
      filters: [{ name: 'STEP模型', extensions: ['stp','step'] }],
      properties: ['openFile'],
    });
    if (!result.canceled && result.filePaths[0]) {
      // 起 Python 子进程
      const { spawn } = require('child_process');
      const proc = spawn('python', [
        'pipeline.py',
        result.filePaths[0],
        '--output-dir', path.join(getModelsPath(), 'occt_output'),
      ]);
      
      proc.stdout.on('data', (data) => {
        console.log(`[OCCT] ${data}`);
        mainWindow.webContents.send('occt-progress', data.toString());
      });
      
      proc.stderr.on('data', (data) => {
        console.error(`[OCCT ERR] ${data}`);
      });
      
      proc.on('close', (code) => {
        if (code === 0) {
          mainWindow.webContents.send('occt-complete', 
            path.join(getModelsPath(), 'occt_output', 'assembly.json'));
        }
      });
    }
  },
}
```

---

## 七、现有项目成果复用表

| 模块 | 源文件 | 复用方式 |
|------|--------|----------|
| **glTF 加载** | `GLTFLoader.js` + `model-loader.js` | 直接加载 OCCT 输出的 .glb |
| **位置图** | `position-map.js` | 透明车壳 + 部件叠加，不变 |
| **分组爆炸引擎** | `explosion-view.js` | `loadAssemblyGroups()` 从 JSON 填充 |
| **手动编组 UI** | `tree-view.js` 多选 + 编组面板 | 自动生成后人工微调方向/阶段 |
| **标注渲染** | `annotation.js` | 序号圆圈 + 推力线保持不变 |
| **PNG 导出** | `export.js` | 逐阶段批量调用 |
| **窗口缩放** | `scene-manager.js` ResizeObserver | 无需修改 |

---

## 八、总工作量与里程碑

| 阶段 | 内容 | 人天 | 里程碑产出 |
|------|------|------|-----------|
| 0 | 基础设施：STP 读取 + glTF 导出 | 3-4 | STP→glb 全链路验证通过 |
| 1 | 装配分析 + 拆装顺序生成 | 15-20 | DAG + 拆卸方向表 |
| 2 | 碰撞检测 + 路径验证 | 10-15 | 无碰撞拆卸路径（90%+ 零件） |
| 3 | 前端适配 + 相机采图 | 8-12 | 逐阶段爆炸动画 + 自动截图 |
| 4 | 集成 + 验证 | 5-8 | 端到端：STP → 多阶段爆炸示意图 |
| **合计** | | **41-59** | |

---

## 九、风险与缓解

| 风险 | 概率 | 缓解措施 |
|------|------|----------|
| 装配约束自动识别准确率 < 80% | 高 | 提供人工审核/调整 UI |
| 复杂拓扑导致 DAG 死锁 | 中 | 死锁回退策略：取入度最小节点 |
| 10GB 整车 STEP 内存溢出 | 中 | STP 天然支持按零件导出；分段处理：按总成拆分，每段独立处理 |
| B-Rep→Mesh 三角面过多 | 中 | 自适应偏差（大件 2mm，小件 0.5mm）+ OCCT 内置 mesh simplifier |
| Python 子进程同步问题 | 低 | IPC 通过 stdout 解析 + 进度条反馈 |
| 大型装配体接触检测 O(n²) 性能 | 中 | 空间划分 (AABB 预筛选) + 并行化 |
