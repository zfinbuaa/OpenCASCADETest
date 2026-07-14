# 2026-07-13 数模清洗 + PMI 功能改进记录

## 一、问题背景

本日解决了以下核心问题：

| # | 问题 | 根因 | 解决方式 |
|---|------|------|---------|
| 1 | 数模清洗 Step 0 全量 5942 零件名称乱码，Step 1 匹配为 0 | `get_shape_name()` 使用 `attr.Dump()` 文本解析，中文丢失 | 改为 `label.GetLabelName()` |
| 2 | 数模清洗无 BOM 匹配时去重全跳过 | Step 1=0 → Step 2 跳过 → Step 3 跳过 | 新增兜底：全量零件做 AABB+质心+体积去重 |
| 3 | 形状去重误用随机采样 → 相同形状无法检测 | `np.random.choice` 打乱点序 → 逐点比较失败 | 改为等距采样 + 坐标排序 + AABB 预筛 |
| 4 | Mesh 采样在不同 Compound 中结果不一致 | `brep_to_mesh` 浮点偏差 | 改为 OCCT B-Rep 物理属性（体积+质心+AABB）直接比较 |
| 5 | 日志中文输出乱码 | Windows 控制台编码 | 添加 `PYTHONLEGACYWINDOWSSTDIO=utf-8` |
| 6 | 装配树被误判为 Compound → `_S001` 分裂 | `IsAssembly()` 返回 False | 新增 `diagnose_assembly_tree()` + 自动诊断日志 |
| 7 | PMI 端子标签无法提取（只认 `T\d{2}`） | 正则限制过严 | 改为提取含数字的任意标签（≤10 字符） |
| 8 | PMI 空间匹配失败（引线源错误 + 索引配对） | 扫描 FreeShape 边 + `leaders[i]→labels[i]` | 新增 `trace_pmi_positions()` 纯文本追踪 + 双向最近邻匹配 |
| 9 | 位置图 Tab 需拆分为维修/电路两个版本 | 单一 Tab | Tab 2 "位置图（维修）" + Tab 3 "位置图（电路）" |

---

## 二、改动文件清单

| # | 文件 | 新增 | 修改 | 删除 | 说明 |
|---|------|------|------|------|------|
| 1 | `index.html` | 1 | 1 | 0 | Tab 重命名 + 新增 |
| 2 | `js/main.js` | ~200 | ~10 | ~15 | 电路面板 + tab 注册 + PMI 回调 + `_showCircuitAnnotations` |
| 3 | `js/annotation.js` | ~50 | ~30 | ~15 | `setPmiLabels()` + 胶囊标签 + 文字居中 |
| 4 | `preload.js` | 1 | 0 | 0 | `runPmiMatch` IPC |
| 5 | `main.js` (Electron) | ~55 | 1 | 0 | `runPmiMatch()` + IPC handler |
| 6 | `pipeline/pmi_diag.py` | ~300 | ~80 | ~100 | 核心重写：解析/追踪/匹配 |
| 7 | `pipeline/xcaf_utils.py` | ~105 | ~20 | ~25 | `diagnose_assembly_tree()` + `get_shape_name` 重写 |
| 8 | `pipeline/model_cleaner.py` | ~40 | ~60 | ~30 | 自动诊断 + 去重回退 + B-Rep 去重 |
| 9 | `pipeline.py` | ~95 | ~5 | ~5 | `--pmi` + `--diag` + `_run_pmi` 诊断 |
| 10 | `pipeline.spec` | 1 | 0 | 0 | `pipeline.pmi_diag` 注册 |
| 11 | `tests/gen_pmi_test_stp.py` | 210 | — | — | 新增 |
| 12 | `tests/test_pmi_regex.py` | 332 | — | — | 新增 |
| | **合计** | **~1390** | **~207** | **~190** | |

---

## 三、前端改动

### 3.1 `index.html` — Tab 结构调整

| Tab | 原内容 | 新内容 |
|-----|--------|--------|
| 2 | `位置图` | `位置图（维修）` |
| 3 | `爆炸图` | `位置图（电路）`（新增，后续 Tab 依次后移） |
| 4 | `拆装方案（可维修性）` | `爆炸图` |
| 5 | `拆装方案（维修手册）` | `拆装方案（可维修性）` |
| 6 | `帮助` | `拆装方案（维修手册）` |
| 7 | — | `帮助` |

### 3.2 `js/main.js` — 电路面板

#### A. 状态管理

- `tabs` 数组新增索引 3：`{ mode: 'position-circuit', tree: null }`
- `shared` 新增 `pmiLabels: null`, `stpPath: null`
- `titles` 数组：`['数模对比', ..., '位置图（维修）', '位置图（电路）', ...]`

#### B. `renderPositionCircuitPanel()`

面板包含四个 section：
1. **数据加载** — "加载PMI标注" 按钮 → IPC → `runPmiMatch`
2. **车壳选择** — 下拉框 + "导入新壳"
3. **可见性** — 全部显示/隐藏/仅显示选中
4. **标注导出** — 显示/清除标注 + 导出 PNG/SVG

**移除项：** BOM 条目 section、标注管理 section（编组按钮）

#### C. `_showCircuitAnnotations()`

```javascript
1. 读取 shared.pmiLabels（[{label, part, leader_pos, dist}, ...]）
2. 每个 label 的 targetWorldPos：mesh BBox 中心（如果 part 有 mesh）或 leader_pos
3. annot.setPmiLabels(labelData)
4. annot.show()
```

### 3.3 `js/annotation.js` — 胶囊标签

#### 新增方法：`setPmiLabels(labels)`

接收 `[{label, partId, targetWorldPos}, ...]`，内部转换为 annotation 对象，设置 `labelText` 字段。

#### 修改 `drawOne()` — 双路径

```javascript
if (labelText) {
    // PMI 胶囊标签：自适应宽度 = measureText().width + 16px
    // 最小宽度 = 24px
    // 两端半圆 + 矩形中段
    // 引线从胶囊边缘出发
    // textAlign='center' + textBaseline='middle'
} else {
    // 数字圆形标签（完全不变的原有逻辑）
}
```

| 参数 | 值 | 说明 |
|------|-----|------|
| `capH` | 24px | 胶囊高度 |
| `capR` | 12px | 两端半圆半径 |
| `padX` | 8px | 文字两侧留白 |
| 字体 | 10px bold | PMI 标签字体 |
| 最小宽度 | `Math.max(24, textW + 16)` | 标签过短时不缩 |

### 3.4 `preload.js` + `main.js` (Electron) — IPC

```javascript
// preload.js
runPmiMatch: (stpPath) => ipcRenderer.invoke('run-pmi-match', stpPath),

// main.js (Electron)
ipcMain.handle('run-pmi-match', async (_event, stpPath) => {
    return await runPmiMatch(stpPath);
});
```

`runPmiMatch(stpPath)`:
- 调用 `resources/pipeline/AutoModel.exe stpPath --pmi --output-dir ...`
- 等待子进程退出
- 读取 `pmi_labels.json` 返回 `{ labels: [...] }`

---

## 四、后端改动

### 4.1 `pipeline/pmi_diag.py` — PMI 核心重写

#### `parse_pmi_text_from_step(filepath)`

| 旧 | 新 |
|-----|-----|
| `T\d{2}$` 只匹配 T+2位数字 | `\d` 含任意数字即保留，标签长度 ≤ 10 |
| 仅 DESCRIPTIVE_REPRESENTATION_ITEM 格式 | 同时支持 `/*PMI:` 注释格式（测试文件） |

**过滤规则：**
- 提取 `\X0\\\w` 到 `\\w` 之间的文本作为标签
- 保留条件：至少含 1 个数字 AND 长度 ≤ 10 字符
- `T01`, `T27`, `UJK01`, `D03` → 保留
- `Wet`, `W`, `S` → 忽略（无数字）

#### `trace_pmi_positions(filepath)` — 新增

纯正则追踪 PMI 实体链（不依赖 OCCT）：

```
DESCRIPTIVE_REPRESENTATION_ITEM('FCF\wT01\w')          ← 文本标签
  → REPRESENTATION (引用 DESC_ITEM)
    → PROPERTY_DEFINITION_REPRESENTATION
      → PROPERTY_DEFINITION
        → CHARACTERIZED_ITEM_WITHIN_REPRESENTATION
          → DRAUGHTING_CALLOUT_ID
            ← ANNOTATION_PLANE('PMI PLANE',..., PLANE_ID, (#D_CALLOUT_ID))
              → PLANE → AXIS2_PLACEMENT_3D → CARTESIAN_POINT(x,y,z)
```

返回：`[(label, x, y, z, dcall_id), ...]`

#### `match_pmi_by_proximity(doc, pmi_text_map, stp_path)`

**重写逻辑：**

1. 调用 `trace_pmi_positions(stp_path)` 获取每个标签的 3D 平面位置
2. `extract_assembly_tree` → `flatten_assembly_tree` → 获取所有零件 shape
3. 对每个有位置的标签：
   - 用 `BRepBuilderAPI_MakeVertex` 构造端点几何
   - 遍历零件 → AABB 粗筛（距离 < size + 20mm）
   - `BRepExtrema_DistShapeShape` 精确最短距离
   - 距离 < INF → 取最近零件
4. 全量输出：有位置的 `{label, part, dist, leader_pos}`，无位置的 `{label, part:"", ...}`

**与旧版差异：**
- 删除 `GetFreeShapes` 边扫描逻辑（旧版扫描零件结构边，不是 PMI 引线）
- 删除 `leaders[i] → labels[i]` 索引配对（旧版无语义关联）
- 新增文本追踪的 PMI 平面位置（来自真正的 ANNOTATION_PLANE）

### 4.2 `pipeline/xcaf_utils.py` — 辅助改动

#### `get_shape_name(label, shape_tool)` — 重写

| 旧 | 新 |
|-----|-----|
| `attr.Dump()` 文本解析 → 中文乱码 | `label.GetLabelName()` → 正确保留 Unicode |

#### `diagnose_assembly_tree(doc)` — 新增

遍历 XCAF 树收集诊断统计，不构建完整节点树。返回：

```python
{
    "total_nodes": 5942,
    "assembly_nodes": 3,
    "compound_nodes": 128,
    "misclassified_compounds": 42,
    "depth_distribution": {"0": 12, "1": 156, ...},
    "shape_type_distribution": {"SOLID": 5814, ...},
    "misclassified_details": [{entry, name, depth, solid_count, sub_shape_count}, ...]
}
```

### 4.3 `pipeline/model_cleaner.py` — 去重逻辑修复

#### 自动诊断输出

Step 0 加载后自动输出 3~4 行诊断日志：

```
Assembly节点: 1  |  Compound节点: 18  |  误判Compound: 11
深度分布: d0=1, d1=25
WARNING: 有 11 个节点被误判为Compound, 层级可能丢失
```

#### B-Rep 物理属性去重

| 检查项 | 旧（Mesh） | 新（B-Rep） |
|--------|-----------|-----------|
| AABB 尺寸 | `np.random.choice` 采样 → 坐标排序 → 逐点 diff | `brepbndlib.Add` + `Bnd_Box.Get()` 直接比较 |
| 质心距离 | 采样点平均 | `BRepGProp.VolumeProperties` 精确质心 |
| 形状相同判定 | 点云相似度 > 99% | 体积比 > 99% |

新增函数 `_shapes_equivalent()` 三重比较（AABB + 质心 + 体积比）。

#### 空匹配回退

```python
all_kept = keep_step1 | keep_step2
if not all_kept:
    _log("  无BOM匹配, 将对全部 {} 个零件进行形状+位置去重".format(len(parts)))
    all_kept = set(range(len(parts)))
```

#### 去重差值诊断

新增近阈值对统计 + AABB 差直方图输出。

### 4.4 `pipeline.py` — 新增模式

- `--diag` 参数 + `_run_diag()`：装配树诊断模式，输出 `diag_report.txt`
- `--pmi` 参数 + `_run_pmi()`：PMI 标注诊断模式，输出 `pmi_report.txt` + `pmi_labels.json`
- 诊断日志包含 `[DIAG]` 前缀的三行中间状态

### 4.5 `main.js` (Electron) — `buildPipelineEnv()`

新增 `PYTHONLEGACYWINDOWSSTDIO=utf-8` 环境变量，解决 Windows 控制台中文输出乱码。

---

## 五、测试文件

### 5.1 `tests/gen_pmi_test_stp.py`

生成测试用 STEP 文件：

- 2 个 OCCT 自由形状零件（`NAUO1_001_左前门线束` + `NAUO1_002_右前门线束`）
- 5 条完整 PMI 实体链（T01, T04, T11, UJK01, W）
- PMI 链格式：DESCRIPTIVE_REPRESENTATION_ITEM → REPRESENTATION → PROPERTY_DEFINITION_REPRESENTATION → PROPERTY_DEFINITION → CHARACTERIZED_ITEM_WITHIN_REPRESENTATION → ANNOTATION_PLANE → PLANE → AXIS2_PLACEMENT_3D → CARTESIAN_POINT
- 自动验证：OCCT 加载 + 标签提取 + 位置追踪 + 空间匹配

```bash
python tests/gen_pmi_test_stp.py
```

### 5.2 `tests/test_pmi_regex.py`

纯正则 PMI 链路诊断（无 OCCT 依赖），可独立在生产环境 Python 3.6+ 上运行。

### 5.3 `tests/test_pmi_geometry.py`

OCCT PMI 引线几何提取验证脚本，验证 `TDF_ChildIterator` 是否能提取 Datum 标签的子 shape 端点。

---

## 六、数据流

```
用户点击 "加载PMI标注"
  ↓
Electron IPC → AutoModel.exe --pmi file.stp
  ↓
_extract_pmi_full():
  1. OCCT API (extract_pmi) → total_datums / total_dimtols / total_notes
  2. 如果三者均为 0 → fallback:
     ├─ parse_pmi_text_from_step(stp_path)
     │   → 正则扫描 DESCRIPTIVE_REPRESENTATION_ITEM
     │   → 提取含数字的标签 (≤10 字符)
     │   → {"T01": ..., "T04": ..., "UJK01": ..., "W"→ignore}
     ├─ trace_pmi_positions(stp_path)
     │   → 正则追踪 DESCRIPTIVE_REPRESENTATION_ITEM → CARTESIAN_POINT
     │   → [("T01", 142.5, 85.7, 33.2), ("T04", 243.8, 112.4, 45.6), ...]
     └─ match_pmi_by_proximity(doc, text_map, stp_path)
         → 对每个标签的 3D 位置，BRepExtrema 最近零件
         → [{label, part, dist, leader_pos}, ...]
  ↓
pmi_labels.json
  ↓
前端 shared.pmiLabels
  ↓
annot.setPmiLabels(labels) → 胶囊形 T01/T04/UJK01 文本标签渲染
```

---

## 七、已知限制

| 限制 | 影响 | 缓解 |
|------|------|------|
| OCCT `GetDatum` API 挂死 | 无法提取 Datum 详情 | 已改为仅计数，详情跳过 |
| `DimTolTool` 不识别 UG AP242 PMI | 生产文件 OCCT 返回 0 datum | 已实现纯文本 `trace_pmi_positions` 回退 |
| PMI 平面位置 ≠ 零件表面位置 | 距离匹配精度取决于标注偏移量 | T01 位置在 PMI 控制框，不在零件面 |
| `flatten_assembly_tree` 压平 Compound | 真实零件名丢失 → `_S001` | `diagnose_assembly_tree` 可诊断，修复待用户反馈 |
| 45+130 个测试文件错误 | 注入 PMI 链中有悬空占位 ID | 不影响功能（零件正常加载，PMI 正常提取） |

---

## 八、构建与部署

### 构建

```bash
# 开发机
build_portable.bat
```

### 测试命令

```bash
# 测试 PMI 诊断
python pipeline.py tests/pmi_test_assembly.stp --pmi --output-dir .\output

# 测试装配树诊断
python pipeline.py file.stp --diag --output-dir .\output

# 生产环境（打包后）
"resources\pipeline\AutoModel.exe" "E:\STEP\file.stp" --pmi --output-dir .\output
```

### UI 测试路径

```
启动 Electron (AutoModel.exe)
→ 文件 > 加载单个 STEP → tests/pmi_test_assembly.stp
→ 切换到 "位置图（电路）" tab
→ 点击 "加载PMI标注"
→ 点击 "显示标注"
→ T01/T04/T11/UJK01 胶囊标签渲染在零件旁
```
