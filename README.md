# 整车三维数模自动拆装方案生成系统

基于 **OpenCASCADE (OCCT) + Three.js + Electron** 的三维装配体自动拆装方案生成与可视化系统。输入 STEP (.stp) 格式的三维装配体模型，自动输出拆装顺序、爆炸方向、碰撞验证报告，并在前端以交互式爆炸图展示。

## 系统架构

```
STEP (.stp) 文件
       │
       ▼
┌──────────────────────────────────────────┐
│  OCCT 几何处理管线 (Python)               │
│                                           │
│  1. XCAF 文档解析 → 装配树 + B-Rep        │
│  2. BRepMesh 离散 → 三角网格 (+法线)      │
│  3. 面接触检测 / 紧固件识别               │
│  4. 碰撞检测 + 拓扑 DAG + 路径规划        │
│  5. glTF 2.0 (.glb) + assembly.json 输出  │
└──────────────┬───────────────────────────┘
               │  parts/*.glb + assembly.json
               ▼
┌──────────────────────────────────────────┐
│  Three.js 前端 (Electron 桌面应用)         │
│                                           │
│  ┌────────┐ ┌────────┐ ┌────────┐       │
│  │ 位置图  │ │ 爆炸图  │ │拆装方案│       │
│  └────────┘ └────────┘ └────────┘       │
└──────────────────────────────────────────┘
```

## 核心功能

### 后端管线 (Python + OCCT)

| 模块 | 功能 |
|------|------|
| `stp_reader.py` | STEP 文件读取，XCAF 文档解析（保留装配层级/颜色/名称） |
| `xcaf_utils.py` | 装配树提取与遍历，4×4 变换矩阵转换 |
| `mesher.py` | B-Rep → 三角网格（含法线），支持偏差控制 |
| `gltf_exporter.py` | glTF 2.0 (.glb) 导出，使用 OCCT 内置导出器 |
| `contact_detector.py` | 面接触检测，R-tree 空间索引加速，接触面积估算 |
| `fastener_identifier.py` | 紧固件识别（体积比 + 接触数启发式） |
| `direction_calc.py` | 工程化爆炸方向计算：面积加权法线 + 重力偏好 + 层级感知 |
| `dag_builder.py` | 拆卸 DAG 生成，方向感知单向阻挡关系 |
| `collision_check.py` | 扫掠碰撞检测，三角网格级 AABB 树 + 二分搜索 |
| `path_searcher.py` | 多方向可行性搜索（26 候选方向） |
| `path_validator.py` | 拆卸路径验证与碰撞报告生成 |
| `assembly_json.py` | assembly.json 输出（零件/编组/阶段/方向） |

### 前端 (Three.js + Electron)

| 模块 | 功能 |
|------|------|
| `scene-manager.js` | Three.js 场景管理（灯光/相机/OrbitControls/自适应） |
| `model-loader.js` | GLB 模型加载 |
| `assembly-loader.js` | assembly.json 解析 → 爆炸编组数据 |
| `explosion-view.js` | 爆炸动画引擎：逐阶段爆炸 / 一键爆炸 / TransformControls 手动拖拽 / 推力线 |
| `tree-view.js` | 层级零件树：点击选择 / 颜色修改 / 阶段徽章 |
| `annotation.js` | 白底黑字圆圈标注 + 水平引线 |
| `export.js` | PNG 截图导出 |
| `main.js` | 主入口：Tab 切换 / 装配加载 / 视口点击高亮 / 管线进度 |

## 工程化爆炸方向算法

方向计算采用工程直觉驱动的多层次策略：

1. **面积加权法线** — 按 `contactArea` 降序取 top-3 接触面加权平均，避免对称法线抵消
2. **26 方向投影搜索** — 将主法线投影到 6 主轴 + 8 体对角线 + 12 面对角线，选最高分方向
3. **重力偏好** — `+Y` 分量 ×1.5 权重，优先向上拆卸（符合工程直觉）
4. **层级感知** — 利用零件 `parent` 字段计算质心连线方向作为候选
5. **智能回退** — 无接触面时按 父件质心方向 → 包围盒最短轴 → `+Y` 优先级回退

## 性能优化

| 优化点 | 方法 | 预期提升 |
|--------|------|----------|
| 接触检测 | R-tree 空间索引跳过远距零件对 | 3-5× |
| 碰撞检测 | 三角网格 AABB 树替代 BRep 布尔运算 | 10-50× |
| 碰撞定位 | 粗步扫描 + 8 次二分搜索 | 4-8× |
| 方向计算 | Bnd_Box 直接取包围盒（无 mesh 转换） | ~100× (该路径) |
| 路径验证 | 预计算 mesh 数据复用 | 整体 2-3× |

## 快速开始

### 环境要求

- **Node.js** ≥ 16
- **Python** ≥ 3.10 + conda
- **OCCT** (pythonocc-core ≥ 7.8)

### 安装

```bash
# 1. OCCT Python 环境
conda create --name=pyoccenv python=3.12
conda activate pyoccenv
conda install -c conda-forge pythonocc-core=7.9.3
pip install numpy

# 2. Electron 前端
npm install
```

### 运行

```bash
# 启动 Electron 应用
npm start

# 或开发模式（自动打开 DevTools）
npm start -- --dev
```

### 命令行管线

```bash
# 预览模式：STP → glb + 基础 JSON（跳过分析）
python pipeline.py input.stp --preview --output-dir ./output/

# 完整模式：STP → 分析 + 碰撞验证 + 拆装方案
python pipeline.py input.stp --output-dir ./output/

# 跳过碰撞检测（加速）
python pipeline.py input.stp --output-dir ./output/ --skip-collision

# 仅验证已有方案
python pipeline.py assembly.json --validate --output-dir ./output/
```

### 测试

```bash
python -m pytest tests/
# 或单独运行
python tests/test_phase1.py
python tests/test_full_pipeline.py
```

## 输出格式

### assembly.json 结构

```json
{
  "name": "装配体名称",
  "sourceFile": "input.stp",
  "parts": [
    {
      "id": "part_name",
      "name": "零件名",
      "glbFile": "parts/part_0000.glb",
      "isFastener": false,
      "disassemblyStage": 2,
      "direction": [0, 1, 0],
      "distanceMultiplier": 1.0,
      "directionConfidence": 0.85,
      "color": [0.8, 0.2, 0.2]
    }
  ],
  "groups": [...],
  "stages": [
    { "stage": 1, "description": "拆除紧固件", "parts": ["bolt_01", "bolt_02"] },
    { "stage": 2, "description": "拆除外覆盖件", "parts": ["panel", "glass"] }
  ],
  "stats": { "totalParts": 10, "totalStages": 3, "totalContacts": 8, "totalFasteners": 2 }
}
```

## 项目结构

```
├── main.js                    # Electron 主进程
├── preload.js                 # IPC 安全桥
├── index.html                 # 前端页面
├── js/                        # Three.js 前端模块
│   ├── main.js                # 入口：Tab/加载/高亮/管线进度
│   ├── scene-manager.js       # 场景/灯光/相机/OrbitControls
│   ├── explosion-view.js      # 爆炸动画 + TransformControls 拖拽
│   ├── tree-view.js           # 层级零件树
│   ├── annotation.js          # 标注渲染
│   ├── assembly-loader.js     # assembly.json 解析
│   ├── model-loader.js        # GLB 加载
│   ├── body-loader.js         # 车壳加载
│   ├── export.js              # PNG 导出
│   ├── position-map.js        # 位置图
│   └── camera-capture.js      # 批量截图
├── pipeline/                  # OCCT Python 管线
│   ├── stp_reader.py          # STEP 读取
│   ├── xcaf_utils.py          # XCAF 装配树工具
│   ├── mesher.py              # B-Rep → 三角网格
│   ├── gltf_exporter.py       # glTF 导出
│   ├── contact_detector.py    # 接触检测 (R-tree 加速)
│   ├── fastener_identifier.py # 紧固件识别
│   ├── direction_calc.py      # 工程化爆炸方向
│   ├── dag_builder.py         # 方向感知拆卸 DAG
│   ├── collision_check.py     # 网格级碰撞检测 (AABB 树)
│   ├── path_searcher.py       # 多方向路径搜索
│   ├── path_validator.py      # 路径验证
│   └── assembly_json.py       # JSON 输出
├── pipeline.py                # 管线入口脚本
├── tests/                     # 测试
├── bodies/                    # 车壳数据
└── package.json
```

## 技术栈

- **几何内核**: OpenCASCADE 7.9 (pythonocc-core)
- **3D 渲染**: Three.js 0.157
- **桌面框架**: Electron
- **数据格式**: STEP → OCCT B-Rep → glTF 2.0 (.glb) + JSON
- **算法**: AABB 空间索引 / 拓扑排序 DAG / 三角网格碰撞检测 / 面积加权方向投影

## License

MIT
