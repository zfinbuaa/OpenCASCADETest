# Changelog 2026-07-08

## 一、视角功能修复

### 1.1 距离系数调整
**文件**: `js/scene-manager.js:102,110`  
**内容**: 聚焦距离系数 `0.65` → `0.85`，相机视角整体拉远，解决视角过近问题。

### 1.2 顶层节点聚焦修复
**文件**: `js/main.js:1188-1203` (onSelect 回调)、`js/main.js:1252-1265` (onCompoundFocus 回调)  
**内容**: 原逻辑只取 `partIds[0]` 的首个 mesh BBox 聚焦。改为计算所有选中 mesh 的合并 BBox 后聚焦，解决双击顶层节点时无法缩放到整体的问题。

### 1.3 新增「位置图截取视角」
**文件**:
- `js/scene-manager.js:124` — 新增 `viewPositionCapture()` 方法，方向向量 `(0.5, 1.8, 0.8)`，从整车右上方俯瞰
- `main.js:167-172` (Electron) — 视图菜单新增「位置图截取视角」及快捷键 `Ctrl+P`
- `preload.js:128-129` — 新增 `onMenuViewPositionCapture` IPC 桥接
- `js/main.js:1751` — 绑定事件

### 1.4 解除垂直面360°旋转限制
**文件**: `js/three-addons/controls/OrbitControls.js:234-254`  
**内容**: 将原有的极角钳制 `[minPolarAngle, maxPolarAngle]`（默认 `[0, π]`）改为极角包裹逻辑——当用户拖拽视角越过天顶或天底时，`phi` 自动翻越并翻转 `theta` 180°，实现所有平面的360°自由旋转。

---

## 二、爆炸图功能重构

### 2.1 核心爆炸逻辑重构
**文件**: `js/explosion-view.js`

| 行号 | 改动 |
|------|------|
| 41-42 | 新增 `_lastExplosionCenter`、`_lastCompounds` 实例变量 |
| 261-269 | 抽取 `_buildCompoundMap(compounds)` 方法 |
| 271-309 | 新增 `_doRadialExplode(centerPoint, distance, compounds)` 共享核心方法，以爆炸中心为中点、编组为单位，计算径向偏移 |
| 406-415 | `setExplosionDistance(dist)` — 拖动滑块时，若已爆炸则调用 `_doRadialExplode` 实时重算位置 |
| 419-427 | `radialExplodeInstant()` — 简化为调用共享方法 + 存储爆炸状态 + 空载提示 |
| 429-432 | `radialExplodeAnimated()` — 改用 `_buildCompoundMap()` 减少重复代码 |

### 2.2 爆炸面板 UI 简化
**文件**: `js/main.js`

| 行号 | 改动 |
|------|------|
| 328 | 删除「逐阶段爆炸」按钮，仅保留「爆炸」按钮 |
| 869-892 | 设置/清除爆炸中心后，若已爆炸则自动以新中心重算偏移 |
| 910-914 | 爆炸按钮改为调用 `radialExplodeInstant`（一次性爆炸） |

### 2.3 功能行为变化
- 点击「爆炸」→ 以当前距离一次性爆炸，兼顾编组（compound）
- 拖动距离滑块 → 已爆炸时部件实时跟随移动
- 设置/清除爆炸中心 → 已爆炸时自动以新中心重算所有偏移

---

## 三、批量生成位置图

### 3.1 标注系统增强
**文件**: `js/annotation.js:52-60`  
**内容**: 新增 `setSingleLabel(partIds, labelText)` 方法，支持程序化设置单一标注标签。

### 3.2 Electron IPC 新增
**文件**: `main.js` (Electron)

| IPC handler | 功能 |
|-------------|------|
| `select-batch-position-files` | 顺序弹窗：选车壳 STP → 选输出目录 → 多选 Excel 文件 |
| `run-bom-preview-cached` | 以已缓存的路径静默运行 BOM 预览管线（无弹窗），返回 assembly.json 路径 |
| `save-batch-file` | 将 PNG dataUrl 解码后写入指定路径（无弹窗） |
| `save-batch-svg` | 将 SVG 字符串写入指定路径（无弹窗） |
| `import-body-from-path` | 以已知 STP 路径导入车壳（无弹窗） |

### 3.3 前端 IPC 桥接
**文件**: `preload.js`

| 方法 | 对应 IPC |
|------|---------|
| `selectBatchPositionFiles` | `select-batch-position-files` |
| `runBomPreviewPipelineCached` | `run-bom-preview-cached` |
| `saveBatchPng` | `save-batch-file` |
| `saveBatchSvg` | `save-batch-svg` |
| `importBodyFromPath` | `import-body-from-path` |

### 3.4 批量位置图核心逻辑
**文件**: `js/main.js`

| 行号 | 函数 | 说明 |
|------|------|------|
| 274 | `renderPositionPanel` | 删除「加载 JSON」按钮，「加载 BOM」→「批量生成位置图」 |
| 770-774 | `bindPositionPanel` | 按钮事件改为 `_batchPositionCapture()` |
| 1673-1748 | `_batchPositionCapture()` | 主编排函数：导车壳 → 选输出路径 → 多选 Excel → 对每表逐组件捕获 |
| 1750-1787 | `_captureSingleComponent()` | 单组件捕获：显隐控制 + 蓝色高亮(R0 G128 B192) + 半透车壳 + 视角切换 + 标注 + PNG/SVG 输出 |
| 1789-1793 | `_captureAnnotatedPNGDataUrl()` | 合成标注 PNG dataUrl |
| 1795-1799 | `_basicSvg()` | SVG 降级方案 |
| 1801-1803 | `_sanitizeFilename()` | 文件名安全处理 |
| 1805-1810 | `path__basename()` | 跨平台提取文件名 |
| 1812-1823 | `_setBodyGroupOpacity()` | 设置车壳透明度 |
| 1825-1836 | `_buildBomEntriesFromAssembly()` | 从 assembly.json 提取 BOM 条目（按 J 列代码分组） |
| 1838-1888 | `_loadModelCoreSilently()` | 静默加载模型（不建树、不调相机） |
| 1890-1909 | `_disposeAllModels()` | 完整释放 Three.js GPU 资源 |

### 3.5 批量生成位置图 — 完整流程
```
点击「批量生成位置图」
 → ① 弹窗: 选车壳 STP → 转换 GLB 并加载
 → ② 弹窗: 选输出目录
 → ③ 弹窗: 多选 Excel 文件
 → 对每个 Excel 文件:
    → 管线加载所有部件
    → 遍历每个 BOM 行 (组件):
       → 仅显示当前组件部件 + 半透明车壳
       → 部件高亮蓝色 (R0 G128 B192)
       → 相机切换到「位置图截取视角」
       → 添加标注标签 (H列名称)
       → 输出 {outputDir}/{表名}_{组件名}.png
       → 输出 {outputDir}/{表名}_{组件名}.svg
    → 释放全部模型资源
 → 完成
```

---

## 四、STEP 装配树名称修复

### 4.1 增强名称查找逻辑
**文件**: `pipeline/xcaf_utils.py:34-78`  
**内容**: 增强 `get_shape_name()` 函数——当当前 label 的 `TDataStd_Name` 为空时，增加三层兜底查询：
1. **父标签查找** — `label.Father().GetLabelName()`，处理 PRODUCT 名称在父标签上的情况
2. **子标签查找** — 遍历 `label.FindChildren()` 查找首个有名称的子标签

**根因**: OCCT XCAF 在读取 AP214/AP242 STEP 文件时，`GetComponents()` 返回的 SHAPE 标签没有 `TDataStd_Name`（名称附着在 PRODUCT 标签上，与 SHAPE 标签不在同一 OCAF 层级），导致深层嵌套组件的名称丢失。

---

## 修改文件总览

| 文件 | 改动次数 | 类别 |
|------|---------|------|
| `js/scene-manager.js` | 3 | 视角 |
| `js/main.js` (frontend) | 10+ | 视角 + 爆炸 + 批量位置图 |
| `main.js` (Electron) | 6 | 菜单 + IPC |
| `preload.js` | 6 | IPC 桥接 |
| `js/three-addons/controls/OrbitControls.js` | 1 | 旋转限制 |
| `js/explosion-view.js` | 7 | 爆炸图 |
| `js/annotation.js` | 1 | 标注 |
| `pipeline/xcaf_utils.py` | 1 | 名称 |
| **合计** | **8 个文件** | |

---

## 九、PMI 标注诊断模式

### 9.1 概述
详见 `.omo/pmi-diag.md`。新增 `--pmi` 命令行参数，用于探测 AP242 STEP 文件中的 PMI（Product Manufacturing Information）标注信息。

### 9.2 新增文件
- `pipeline/pmi_diag.py` — PMI 提取核心逻辑，遍历 `DimTolTool` / `NotesTool` 的 Datum/DimTol/Note 三类标注，通过 `GetRefShapeLabel()` 反查关联零件

### 9.3 修改文件
- `pipeline.py` — 新增 `--pmi` 参数 + `_run_pmi()` 函数（+35 行）

### 9.4 使用方式
```bash
# 生产环境
AutoModel.exe "file.stp" --pmi --output-dir .\output

# 开发环境
python pipeline.py "file.stp" --pmi --output-dir .\output
```
输出 `pmi_report.txt` 到 output 目录，同时在 Electron 日志面板显示。

---

## 十、位置图（电路）功能开发

详见 `.omo/position-circuit-dev.md`。

### 10.1 概述
Tab 2 "位置图" 重命名为 "位置图（维修）"，新增 Tab 7 "位置图（电路）"。电路位置图的标注使用 PMI 端子文本（T01/T04/T11）替代数字标签。

### 10.2 前端
- `index.html` — tab 重命名 + 新 tab
- `js/main.js` — 电路面板（+170 行）、PMI 匹配 IPC、文本标注回调
- `js/annotation.js` — `setPmiLabels()` 方法，`drawOne()` 支持文本标签
- `preload.js` / `main.js` (Electron) — `runPmiMatch` IPC 桥接

### 10.3 后端
- `pipeline/pmi_diag.py` — `parse_pmi_text_from_step()` 正则提取端子号、`match_pmi_by_proximity()` 空间匹配、`extract_pmi_full()` fallback、`GetDatum` API 安全修复
- `pipeline.py` — `_run_pmi` 输出 `pmi_labels.json`

### 10.4 面板差异
| 功能 | 位置图（维修） | 位置图（电路） |
|------|-------------|-------------|
| BOM 条目 | ✓ | ✗（移除） |
| 标注管理（编组） | ✓ | ✗（移除） |
| PMI 端子标注 | ✗ | ✓ |
| 车壳选择 | ✓ | ✓ |
| 可见性 | ✓ | ✓ |

## 十一、2026-07-13 数模清洗 + PMI 功能改进

详见 `.omo/pmi-cleanup-fixes.md`。

### 修复项
- 中文名称提取（`get_shape_name` 改用 `label.GetLabelName()`）
- 形状去重算法（Mesh 采样 → B-Rep 物理属性比较）
- 日志输出编码（`PYTHONLEGACYWINDOWSSTDIO=utf-8`）
- 装配树自动诊断（`--diag` 参数）

### 新增功能
- "位置图（电路）" Tab（Tab 3）
- PMI 端子文本标签（T01/T04/T11/UJK01 等）
- 胶囊形自适应标注框
- PMI 文本解析 + 3D 平面位置追踪 + 空间匹配
