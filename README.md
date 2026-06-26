# 整车三维数模自动拆装方案生成系统

基于 **OpenCASCADE (OCCT) + Three.js + Electron** 的三维装配体自动拆装方案生成与可视化系统。输入 STEP (.stp) 格式的三维装配体模型，自动输出拆装顺序、爆炸方向、碰撞验证报告，并在桌面前端以交互式爆炸图展示。

---

## 系统架构

```
STEP (.stp) 文件
       │
       ▼
┌─────────────────────────────────────────┐
│  OCCT 几何处理管线 (Python)              │
│                                          │
│  1. XCAF 文档解析 → 装配树 + B-Rep       │
│  2. BRepMesh 离散 → 三角网格 (+法线)     │
│  3. 面接触检测 / 紧固件识别              │
│  4. 碰撞检测 + 拓扑 DAG + 路径规划       │
│  5. glTF 2.0 (.glb) + assembly.json 输出 │
└──────────────┬──────────────────────────┘
               │  parts/*.glb + assembly.json
               ▼
┌─────────────────────────────────────────┐
│  Three.js 前端 (Electron 桌面应用)        │
│                                          │
│  ┌────────┐ ┌────────┐ ┌────────┐      │
│  │ 位置图  │ │ 爆炸图  │ │拆装方案│      │
│  └────────┘ └────────┘ └────────┘      │
└─────────────────────────────────────────┘
```

---

## 功能页面

前端有三个 Tab 页面，共享同一份模型数据和 3D 场景，各自维护独立的爆炸/树状态。

### 位置图

**用途**：以原始装配位置查看全部零件，不产生任何位移。

**操作流程**：

1. 用户通过菜单 `文件 → 加载单个 STEP 文件`（Ctrl+O）或 `文件 → 通过表格加载多个 STEP 文件`（Ctrl+B）加载模型
2. 后端管线执行**预览模式**：
   - 读取 STEP → 提取装配树 → B-Rep 转三角网格 → 导出 .glb 文件 → 生成 assembly.json
   - 预览模式不执行接触检测、DAG 构建等分析步骤，仅加载模型
3. 前端加载 .glb 模型，所有零件以装配位置展示
4. 用户在左侧**结构树**中点击零件 → 3D 视口高亮对应部件并自动聚焦
5. 可在右侧面板切换车壳（body shell）叠加显示，帮助定位零件在实际车辆中的位置

### 爆炸图

**用途**：查看零件的爆炸分解视图，支持手动调整爆炸程度和位置。

**操作流程**：

1. 加载装配数据后，切换到"爆炸图"标签页
2. 系统根据每个零件的拆卸方向和距离系数计算爆炸位移
3. 通过右侧面板的滑块控制爆炸程度（0% ~ 100%），或点击"一键爆炸"立即展开全部
4. 可逐阶段查看：先拆紧固件 → 再拆外层 → 逐步到内层
5. 支持 TransformControls 手动拖拽调整单个零件在爆炸视图中的位置
6. 可选显示**推力线**（thrust line），以箭头形式标注每个零件的爆炸方向

**爆炸距离计算**：

- 前端基准爆炸距离：150 mm
- 每个零件的实际爆炸距离 = 150 mm × `distanceMultiplier`
- `distanceMultiplier` 由管线后端根据碰撞检测的 `max_safe_distance` 计算：`multiplier = max(0.05, safe_distance / 150)`
- 因此，一个能自由移动 100 mm 的零件在视觉上爆炸 100 mm；只能移动 30 mm 的零件爆炸 30 mm

### 拆装方案

**用途**：生成并查看有序的拆卸序列。有两种工作模式。

---

#### 模式一：全装配拆装方案

为整个装配体生成完整的分阶段拆卸序列。

**操作流程**：

1. 用户通过菜单 `管线 → 生成拆装方案`（Ctrl+G）触发
2. 后端执行完整的 **8 步分析管线**（见下方详细流程）
3. 完成后，右侧面板展示分阶段的拆卸顺序列表
4. 点击"逐阶段拆卸"可在 3D 视图中按顺序动画演示拆卸过程

#### 模式二：单零件依赖链分析

针对用户指定的某一个目标零件，计算"要拆这个零件必须先拆哪些"的完整依赖链条。

**操作流程**：

1. 用户在左侧**结构树**中选中一个目标零件（状态栏显示 `选中: Part_X (N 零件)`）
2. 切换到"拆装方案"标签页
3. 点击按钮 **`选中目标 → 分析拆卸链`**
4. 后端执行**依赖链分析**，具体过程：

   ```
   用户选中目标零件 Part_X
     ↓
   [1] 为目标零件搜索最优拆卸方向
       - 从 26 个候选方向中选出与预计算方向最相似的 8 个
       - 并行碰撞检测每个方向，统计各方向的阻塞零件数
       - 用光束搜索（K=4）递归模拟前 4 个最佳方向的总拆卸成本
       - 选择总拆卸成本最低的方向
     ↓
   [2] 在该方向下找出直接阻挡 Part_X 的零件列表
     ↓
   [3] 对每个阻挡零件递归执行步骤 [1]-[3]
     ↓
   [4] 递归到底后形成一条有序拆卸链：
       Stage 1: 拆外层阻挡件_A (必须先拆)
       Stage 2: 拆中间阻挡件_B
       Stage 3: 拆目标零件 Part_X (最后拆)
     ↓
   [5] 输出依赖链结果
   ```

5. 前端右侧面板展示：
   - **依赖链概要**：总零件数、可行数、阻塞数
   - **方向对比表**：每个候选方向的阻塞零件数、递归总拆卸成本、是否被选中（★）
   - **逐阶段拆卸顺序表**：标注每个阶段拆哪些零件（"阻挡件"或"目标件"）
6. 点击 **`AI最佳拆装路径`** 可在 3D 视图中逐阶段动画演示拆卸链

---

## 完整拆装方案生成流程

以下是一次完整的"生成拆装方案"过程中，管线按顺序执行的 8 个步骤：

```
输入：STEP (.stp) 装配体文件

[1/8] 读取 STEP
      STEPCAFControl_Reader 解析 STP 文件 → XCAF 文档
      检测文件单位（mm/inch/meter），必要时自动缩放

[2/8] 提取装配树
      遍历 XCAF 装配结构 → 层级树
      扁平化为叶零件列表 + 每个零件的世界空间 4×4 变换矩阵
      支持子装配名称匹配（用于 BOM 模式）

[3/8] 网格化
      对每个零件的 B-Rep 形状做 BRepMesh_IncrementalMesh 离散
      产出：顶点坐标 + 三角形索引 + 逐三角形法线
      线性偏差默认 1.0mm，角偏差默认 0.5 rad

[4/8] 面接触检测
      AABB 粗检 → AABB 树精检（三角面片级分离轴定理）
      多线程并行（线程池 16 线程）
      产出：接触面列表（零件A, 零件B, 接触点, 平均法线, 最小距离, 接触面积）

[5/8] 紧固件识别
      基于启发式规则：体积比 < 0.5% + 接触 ≥ 2 个其他零件
      被识别为紧固件的零件会在 Stage 1 优先拆卸

[6/8] 计算拆卸方向
      为每个零件计算工程化拆卸方向：
        面积加权法线 → 26 方向投影搜索 → 重力偏好(+Y×1.5) → 层级感知
        无接触面时按 父件质心方向 → 包围盒最短轴 → +Y 回退

[7/8] 构建拆卸 DAG
      Stage 1：优先尝试拆除所有紧固件（被阻塞的推迟到后续阶段）
      Stage 2+：逐件碰撞检测，从外向内排序
        每个零件沿其拆卸方向做碰撞检测：
          - 100mm 内无碰撞 → 该零件可拆除
          - 有碰撞 → 标记为阻塞，剩余零件拆除后重新检测
      产出：有序的分阶段拆卸序列

[8/8] 碰撞验证 + 输出
      对新生成的方案做完整的逐阶段碰撞验证
      输出：
        assembly.json（零件/编组/阶段/方向/距离系数）
        碰撞验证报告（report.txt）
        parts/*.glb（每个零件的 3D 模型文件）
```

**碰撞检测的物理含义**：对每个零件，沿其拆卸方向扫掠 100mm（默认值，可通过 `--explosion-distance` 配置），用 AABB 树做三角面片级碰撞检查。不是一个半径 100mm 的球体，而是一个沿方向延伸 100mm 的扫掠通道。

---

## BOM 多文件加载模式

当装配体的零件分散在多个 STEP 文件中时，通过 Excel 表格（BOM）统一加载。

**操作流程**：

1. 用户准备一个 Excel 表格（.xlsx），H 列为零件名称，J 列为零件编码
2. 菜单 `文件 → 通过表格加载多个 STEP 文件`（Ctrl+B）→ 选择 BOM Excel 文件
3. 管线读取 BOM → 找到对应的 STEP 文件 → 依次加载每个零件 → 合并为一个装配体
4. 后续所有操作（位置图 / 爆炸图 / 拆装方案 / 依赖链分析）与单文件模式完全一致

---

## 碰撞检测中的 100mm 阈值

碰撞检测时，零件沿拆卸方向扫掠 **100mm**（默认值）。这个值的物理含义是：如果零件能沿该方向无障碍移动 100mm，则视为"可以拆除"。

对于汽车部件而言，10cm 已经是一个较大的位移：螺栓通常只需退出几个毫米的螺纹长度，大型覆盖件 100mm 也已足够脱离安装位。该阈值可通过命令行参数 `--explosion-distance` 调整。

---

## 安装与运行

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
pip install numpy openpyxl trimesh

# 2. Electron 前端
npm install
```

### 开发运行

```bash
npm start        # 启动 Electron 应用
npm start -- --dev  # 开发模式（自动打开 DevTools）
```

### 构建免安装便携包

```bash
build_portable.bat
```

脚本自动完成：清理 → PyInstaller 打包管线引擎 → electron-builder 打包前端 → 生成

产物：
```
release/
├── win-unpacked/                ← 便携文件夹
│   ├── AutoModel.exe            ← 双击启动
│   ├── resources/pipeline/      ← Python 管线引擎
│   └── ...
└── AutoModel_x.x.x_portable.zip ← 分发用压缩包
```

部署方式：将 `win-unpacked/` 整个文件夹复制到目标机器，双击 `AutoModel.exe` 即可运行，无需安装 Python 或 conda 环境。

### 命令行管线

```bash
# 预览模式：STP → glb + 基础 JSON（跳过分析）
python pipeline.py input.stp --preview --output-dir ./output/

# 完整模式：STP → 分析 + 碰撞验证 + 拆装方案
python pipeline.py input.stp --output-dir ./output/

# 跳过碰撞检测（仅做几何爆炸）
python pipeline.py input.stp --output-dir ./output/ --skip-collision

# 自定义碰撞检测距离
python pipeline.py input.stp --output-dir ./output/ --explosion-distance 200

# 仅验证已有方案
python pipeline.py assembly.json --validate --output-dir ./output/
```

### 测试

```bash
python -m pytest tests/
```

---

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

字段说明：
- `disassemblyStage`：零件在第几阶段被拆除
- `direction`：拆卸方向（3D 单位向量）
- `distanceMultiplier`：视觉爆炸距离系数（× 150mm = 实际爆炸距离）
- `directionConfidence`：方向计算置信度（0-1）
- `isFastener`：是否被识别为紧固件（螺栓/螺母）

---

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
│   ├── contact_detector.py    # 接触检测 (AABB 加速)
│   ├── fastener_identifier.py # 紧固件识别
│   ├── direction_calc.py      # 工程化爆炸方向
│   ├── dag_builder.py         # 方向感知拆卸 DAG
│   ├── collision_check.py     # 网格级碰撞检测 (AABB 树)
│   ├── path_searcher.py       # 多方向路径搜索
│   ├── path_validator.py      # 路径验证
│   ├── dependency_chain.py    # 依赖链分析
│   └── assembly_json.py       # JSON 输出
├── pipeline.py                # 管线入口脚本
├── build_portable.bat         # 一键打包脚本
├── VERSION.txt                # 版本号
├── tests/                     # 测试
├── bodies/                    # 车壳数据
└── package.json
```

---

## 技术栈

- **几何内核**：OpenCASCADE 7.9 (pythonocc-core)
- **3D 渲染**：Three.js 0.157
- **桌面框架**：Electron
- **数据格式**：STEP → OCCT B-Rep → glTF 2.0 (.glb) + JSON
- **打包**：PyInstaller + electron-builder
- **算法**：AABB 空间索引 / 拓扑排序 DAG / 三角网格碰撞检测 (分离轴定理) / 面积加权方向投影 / 光束搜索 K=4

## License

LGPL-3.0
