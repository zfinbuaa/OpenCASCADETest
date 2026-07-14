# 位置图（电路）功能开发记录

## 日期
2026-07-09

## 概述

新增"位置图（电路）"功能页，支持从 AP242 STEP 文件中提取 PMI 端子标注（T01/T04/T11 等），通过空间匹配算法将 PMI 引线端点与零件关联，在前端以文本标签形式标注。

---

## 改动文件清单

| # | 文件 | 新增行 | 修改行 | 说明 |
|---|------|--------|--------|------|
| 1 | `index.html` | 1 | 1 | tab 重命名 + 新增 |
| 2 | `js/main.js` | ~170 | ~5 | tab 注册、面板渲染、标注回调、PMI 匹配 |
| 3 | `js/annotation.js` | ~40 | ~15 | PMI 文本标签模式 `setPmiLabels()` |
| 4 | `preload.js` | 1 | 0 | `runPmiMatch` IPC 桥接 |
| 5 | `main.js` (Electron) | ~50 | 0 | `runPmiMatch()` + IPC handler |
| 6 | `pipeline/pmi_diag.py` | ~180 | ~10 | PMI 文本解析 + 空间匹配 + API 安全修复 |
| 7 | `pipeline.py` | ~15 | 2 | `_run_pmi` 输出 `pmi_labels.json` |
| | **合计** | **~457** | **~33** | |

---

## 一、前端改动

### 1.1 `index.html` — Tab 结构调整

| 修改项 | 原内容 | 新内容 |
|--------|--------|--------|
| Tab 2 | `位置图` | `位置图（维修）` |
| Tab 7（新增） | — | `位置图（电路）` |

```html
<button class="tab" data-tab="2">位置图（维修）</button>
<!-- ... -->
<button class="tab" data-tab="7">位置图（电路）</button>
```

### 1.2 `js/main.js` — 核心逻辑

#### A. 状态管理

- `tabs` 数组新增索引 7：`{ mode: 'position-circuit', tree: null }`
- `shared` 新增 `pmiLabels` 和 `stpPath` 字段

#### B. `renderPositionCircuitPanel()`

电路面板包含：
- **数据加载** — "加载PMI标注" 按钮（调用 Electron PMI 匹配管线）
- **车壳选择** — 下拉框 + "导入新壳" 按钮
- **可见性** — 全部显示/隐藏/仅显示选中
- **标注导出** — 显示/清除标注 + 导出 PNG/SVG

**移除项：**
- BOM 条目 section（完全删除）
- 标注管理 section（"勾选部件→生成标注"、"清空标注"、compound 预览）

#### C. `bindPositionCircuitPanel()`

绑定回调：
- `btn-load-circuit` → `window.electronAPI.runPmiMatch(stpPath)` → 解析结果 → `_showCircuitAnnotations()`
- 车壳选择、导入新壳（同维修版逻辑）
- 可见性控制（同维修版逻辑）
- 标注显示/清除/导出

#### D. `_showCircuitAnnotations()`

```javascript
1. 读取 shared.pmiLabels（[{label, part, dist, leader_pos}, ...]）
2. 每个 label 的 targetWorldPos：
   - 如果 part 有对应 mesh → 使用 mesh BBox 中心
   - 否则使用 leader_pos
3. annot.setPmiLabels(labelData)
4. annot.show()
```

### 1.3 `js/annotation.js` — PMI 文本标签

#### 新增方法：`setPmiLabels(labels)`

```javascript
setPmiLabels(labels) {
  this.annotations = labels.map((item, i) => ({
    partId: item.partId,
    partName: item.label,        // PMI 文本（T01/T04/T11）
    worldPos: new THREE.Vector3(...item.targetWorldPos),
    index: i,
    labelText: item.label,       // 标志：使用文本标签
    partIds: new Set([item.partId]),
  }));
}
```

#### 修改 `drawOne()` — 文本标签渲染

```javascript
if (labelText) {
  ctx.font = 'bold 9px "Microsoft YaHei", sans-serif';
  ctx.fillText(labelText, circleX, cy);    // 渲染 T01/T04/T11
} else {
  ctx.font = 'bold 12px ...';
  ctx.fillText(String(number), circleX, cy);  // 渲染数字
}
```

### 1.4 `preload.js` + `main.js` (Electron) — IPC 桥接

```javascript
// preload.js
runPmiMatch: (stpPath) => ipcRenderer.invoke('run-pmi-match', stpPath),

// main.js (Electron)
ipcMain.handle('run-pmi-match', async (_event, stpPath) => {
  return await runPmiMatch(stpPath);
});
```

`runPmiMatch(stpPath)`：
- 调用 `resources/pipeline/AutoModel.exe stpPath --pmi --output-dir ...`
- 等待子进程退出 → 读取 `pmi_labels.json` → 返回 `{ labels: [...] }`

---

## 二、后端改动

### 2.1 `pipeline/pmi_diag.py`

#### 新增函数 1：`parse_pmi_text_from_step(filepath)`

```python
def parse_pmi_text_from_step(filepath):
    """
    扫描 STEP 文件的 DESCRIPTIVE_REPRESENTATION_ITEM，
    提取 PMI 标签文本。
    
    正则：r"DESCRIPTIVE_REPRESENTATION_ITEM\('equivalent unicode string',\s*'FCF.*?X0\\\w([A-Za-z0-9]+)\\w'"
    
    过滤：只保留匹配 ^T\d{2}$ 的标签（如 T01, T04, T11）
    
    返回: {"T01": "FCF\\w\\X2\\23E4\\X0\\\\wT01\\w", ...}
    """
```

**规则说明：**
- `'FCF\\w\\X2\\23E4\\X0\\\\wT01\\w'` → 提取 `T01` ✓
- `'FCF\\w\\X2\\2316\\X0\\\\wWet'` → 提取 `Wet` → 不匹配 `T\d{2}` → 忽略 ✓

#### 新增函数 2：`match_pmi_by_proximity(doc, pmi_text_map)`

空间匹配算法，核心流程：

```
1. 遍历 DimTolTool.GetDatumLabels() / GetDimTolLabels()
2. 对每个 PMI 标签，通过 TDF_ChildIterator 提取子 shape 的直线边
3. 识别引线终止端点（最短直线边，指向零件表面）
4. 通过 pmi_text_map 按索引对应 PMI 标签文本
5. flatten_assembly_tree → 获取所有零件 shape + AABB 中心
6. 对每个引线端点：
   a. AABB 粗筛：跳过距离 > size + max_dist_mm*2 的零件
   b. BRepExtrema_DistShapeShape(leader_tip, part_shape) 精确距离
   c. 距离 < max_dist_mm(默认10mm) 且最近 → 匹配成功
7. 返回: [{label, part, dist, leader_pos}, ...]
```

**关键技术点：**
- `BRepBuilderAPI_MakeVertex` 构造引线端点几何体
- `BRepExtrema_DistShapeShape` 计算点到形状的最短距离
- AABB 粗筛加速（跳过大尺寸形状对）

#### 新增函数 3：`extract_pmi_full(doc, stp_path)`

```python
def extract_pmi_full(doc, stp_path=None):
    result = extract_pmi(doc)       # 先尝试 OCCT API
    if stp_path and no_occt_results:
        pmi_text = parse_pmi_text_from_step(stp_path)
        if pmi_text:
            matches = match_pmi_by_proximity(doc, pmi_text)
            result["match_results"] = matches
    return result
```

#### API 安全修复

`GetDatum()` 调用从全量遍历改为计数模式（API 挂死风险）：
```python
# 旧：遍历所有 datum → GetDatum(lab) 详细提取 → 可能挂死
# 新：仅调用 GetDatumLabels() 计数，不提取详细信息
```

### 2.2 `pipeline.py` — `_run_pmi()` 输出 JSON

```python
# 新增：输出 pmi_labels.json 供前端读取
if match_results:
    import json
    labels_json = os.path.join(args.output_dir, "pmi_labels.json")
    with open(labels_json, "w", encoding="utf-8") as f:
        json.dump({"labels": match_results}, f, ensure_ascii=False, indent=2)
    log("PMI_MATCH_JSON: %s" % labels_json.replace("\\", "/"))
```

---

## 三、数据流

```
用户点击 "加载PMI标注"
  ↓
Electron → IPC 'run-pmi-match' → runPmiMatch(stpPath)
  ↓
AutoModel.exe --pmi file.stp
  ↓
parse_pmi_text_from_step(filepath)
  → 正则扫描 DESCRIPTIVE_REPRESENTATION_ITEM
  → 提取 T\d{2} 标签 → {"T01": ..., "T04": ...}
  ↓
match_pmi_by_proximity(doc, text_map)
  → TDF_ChildIterator 提取引线端点
  → BRepExtrema_DistShapeShape 空间距离匹配
  → [{label, part, dist, leader_pos}, ...]
  ↓
pmi_labels.json
  ↓
前端 shared.pmiLabels
  ↓
annot.setPmiLabels(labels) → 渲染 T01/T04/T11 文本圆圈标签
```

---

## 四、验证结果

| 测试 | 结果 |
|------|------|
| NIST AP242 文件 PMI 文本解析 | 0 个 T\d{2}（NIST 文件无名）✓ |
| NIST 文件引线端点提取 | 76 个 Datum 全部有引线几何 ✓ |
| 空间匹配（模拟 T01/T04） | 2 个匹配成功（dist=89mm）✓ |
| 合成测试 STP（无 PMI） | 0 datums，不触发 fallback ✓ |
| `extract_pmi` API 安全性 | GetDatum 不再挂死 ✓ |

---

## 五、遗留项

| 项目 | 说明 |
|------|------|
| 批量导出功能 | "位置图（电路）"的批量导出待后续开发 |
| 标注管理保留 | "位置图（维修）"的标注管理功能不受影响 |
| 引线距离阈值 | 当前 `max_dist_mm=10.0`，用户实际数据可能需要调整 |
| 端子号匹配规则 | 当前仅支持 `T\d{2}` 模式，可扩展为配置项 |
