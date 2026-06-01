# 修复计划：模型不显示 + 结构树勾选 + BOM 导入

## 问题 1：模型不显示

### 根因确认
CSP 中 importmap 的 sha256 哈希不匹配：
- 当前 HTML 中: `sha256-YGiNOixOp7U3E/31mZrq6xkeoXzYfrpiEvR1wh6RfSQ=`
- 实际正确值: `sha256-m7AOZhYFGKsHQCYq6RCE0mEsf3JZFENwaKwULRnIZFo=`

浏览器拒绝执行 importmap → Three.js 模块加载失败 → 整个前端不工作。

### 修复方案：移除 importmap，改用相对路径

**Step 1**: 删除 `index.html` 中的 importmap 块（第 132-139 行），CSP 改为 `script-src 'self'`（无哈希）。

**Step 2**: 修改所有 `from 'three'` 引用为相对路径：

| 文件 | 旧路径 | 新路径 |
|------|--------|--------|
| `js/main.js:8` | `from 'three'` | `from '../node_modules/three/build/three.module.js'` |
| `js/explosion-view.js:5` | `from 'three'` | `from '../node_modules/three/build/three.module.js'` |
| `js/scene-manager.js:6` | `from 'three'` | `from '../node_modules/three/build/three.module.js'` |
| `js/annotation.js:7` | `from 'three'` | `from '../node_modules/three/build/three.module.js'` |
| `js/position-map.js:8` | `from 'three'` | `from '../node_modules/three/build/three.module.js'` |
| `js/camera-capture.js:7` | `from 'three'` | `from '../node_modules/three/build/three.module.js'` |
| `js/three-addons/controls/OrbitControls.js:12` | `from 'three'` | `from '../../../node_modules/three/build/three.module.js'` |
| `js/three-addons/controls/TransformControls.js:21` | `from 'three'` | `from '../../../node_modules/three/build/three.module.js'` |
| `js/three-addons/loaders/GLTFLoader.js:66` | `from 'three'` | `from '../../../node_modules/three/build/three.module.js'` |
| `js/three-addons/utils/BufferGeometryUtils.js:12` | `from 'three'` | `from '../../../node_modules/three/build/three.module.js'` |

`three/addons/` 的引用（`./three-addons/...`）已经是相对路径，无需修改。

**Step 3**: 恢复 `sandbox: false`（因为 Electron sandbox 模式下 preload 的 `require` 行为不确定，可能阻断 IPC）。

修改 `main.js` webPreferences:
```js
webPreferences: {
  preload: path.join(__dirname, 'preload.js'),
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: false,
  webSecurity: true,
  allowRunningInsecureContent: false,
},
```

---

## 问题 2：结构树勾选层级不正常

### 根因
`tree-view.js` 的 `_toggleCheck` 是单选模型：
- `_checkedNodeId` 是单值，勾选新节点会替换旧节点
- `_refreshCheckboxes` 只匹配 `nodeId === _checkedNodeId`，子节点不显示勾选
- 不支持同时勾选多个节点

### 修复方案：三态多选模型

**Step 1**: 数据结构改造

```js
// 移除
this._checkedNodeId = null;

// 新增
this._checkedPartIds = new Set();    // 已保留，改为累加
this._lastCheckedNodeId = null;      // 最近操作的节点ID（给 getCheckedNodeId 用）
this._nodeIdToPartIds = new Map();   // nodeId → Set(partId) 缓存
```

**Step 2**: `_toggleCheck` 重写

```js
_toggleCheck(node) {
  const nodePartIds = new Set();
  this._collectPartIds(node, nodePartIds);
  const allChecked = [...nodePartIds].every(pid => this._checkedPartIds.has(pid));

  if (allChecked) {
    for (const pid of nodePartIds) this._checkedPartIds.delete(pid);
    this._lastCheckedNodeId = null;
  } else {
    for (const pid of nodePartIds) this._checkedPartIds.add(pid);
    this._lastCheckedNodeId = node.id;
  }

  this._refreshCheckboxes();
  if (this.callbacks.onCheckChange) {
    this.callbacks.onCheckChange(this._lastCheckedNodeId, this._checkedPartIds);
  }
}
```

**Step 3**: `_refreshCheckboxes` 三态显示

```js
_refreshCheckboxes() {
  for (const row of this.container.querySelectorAll('.tree-node')) {
    const cb = row.querySelector('.tree-check');
    if (!cb) continue;
    const nodeId = row.dataset.nodeId;
    const nodePartIds = this._nodeIdToPartIds.get(nodeId);
    if (!nodePartIds || nodePartIds.size === 0) {
      cb.checked = false;
      cb.indeterminate = false;
      continue;
    }
    let checked = 0;
    for (const pid of nodePartIds) if (this._checkedPartIds.has(pid)) checked++;
    cb.checked = (checked === nodePartIds.size);
    cb.indeterminate = (checked > 0 && checked < nodePartIds.size);
  }
}
```

**Step 4**: `build()` 中构建索引

```js
build(hierarchy, parts, stages = []) {
  ...
  this._buildNodeIndex();
  ...
}

_buildNodeIndex() {
  this._nodeIdToPartIds = new Map();
  const visit = (node) => {
    const ids = new Set();
    this._collectPartIds(node, ids);
    this._nodeIdToPartIds.set(node.id, ids);
    for (const c of node.children || []) visit(c);
  };
  for (const r of this.hierarchy) visit(r);
}
```

**Step 5**: `getCheckedNodeId` 兼容

```js
getCheckedNodeId() {
  return this._lastCheckedNodeId;
}
```

**Step 6**: `js/main.js` 中的 `onCheckChange` 回调适配

当前 `onCheckChange: (nodeId, partIds)` 中 `partIds` 从 `_checkedPartIds` 传入已是 Set。改为多选后，`partIds` 就是整个 `checkedPartIds`（包含多个节点的零件），兼容逻辑不变。

但需注意：`_rebuildExplosionGroups` 中的编组合并逻辑应基于 `checkedPartIds`，而非单个节点 ID。当前逻辑已经如此（第 554-581 行），无需修改。

---

## 问题 3：BOM 导入 test.xlsx 失败

### 根因
1. `bom_loader.py:91` 只尝试 `code + ".stp"`，不尝试大写后缀 `.STEP` / `.STP`
2. 实际文件是 `Gusto-grabcad.STEP`（大写 .STEP）
3. test.xlsx 中 Column H 为空（None），导致 `target_name = ""`
4. 错误信息不够帮助（不列出目录中有哪些 STP 文件）

### 修复方案

**Step 1**: `bom_loader.py` — 后缀大小写不敏感探测

替换 `bom_loader.py` 中 `stp_path = os.path.join(models_dir, code + ".stp")` 区域：

```python
stp_path = None
# Try common extensions (case-sensitive first, then fallback)
for ext in (".stp", ".STP", ".step", ".STEP", ".Step"):
    candidate = os.path.join(models_dir, code + ext)
    if os.path.exists(candidate):
        stp_path = candidate
        break

# Fallback: case-insensitive directory scan
if stp_path is None:
    try:
        code_lower = code.lower()
        for entry in os.listdir(models_dir):
            base, ext = os.path.splitext(entry)
            if ext.lower() in ('.stp', '.step') and base.lower() == code_lower:
                stp_path = os.path.join(models_dir, entry)
                break
    except OSError:
        pass

if stp_path is None:
    # Default for error reporting
    stp_path = os.path.join(models_dir, code + ".stp")
```

**Step 2**: `validate_bom_entries` — 增加 STP 文件提示

```python
def validate_bom_entries(entries, models_dir=None):
    ...
    if missing and models_dir and os.path.isdir(models_dir):
        try:
            avail = sorted([f for f in os.listdir(models_dir)
                            if f.lower().endswith(('.stp', '.step'))])[:20]
            if avail:
                lines.append("  Available STP/STEP files in models_dir:")
                for f in avail:
                    lines.append("    - " + f)
        except OSError:
            pass
    return valid, missing, lines
```

**Step 3**: `bom_loader.py` — 空 target_name 处理

当 `target_name` 为空但 `code` 存在时，用 `code` 作为 `target_name` 的回退值：

```python
if not target_name and code:
    target_name = code
```

---

## 执行顺序

1. 修复 importmap（移除 + 改相对路径）← 恢复界面
2. 恢复 sandbox: false ← 确保稳定性
3. 修复结构树三态多选 ← 功能修复
4. 修复 BOM 后缀匹配 ← 功能修复
5. 验证：启动应用 → 加载 Gusto assembly.json → 确认可见 + 树勾选 + BOM
