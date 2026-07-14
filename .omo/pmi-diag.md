# PMI 诊断模式 (`--pmi`)

## 概述

数模清洗管线新增 `--pmi` 参数，用于探测 AP242 STEP 文件中的 PMI（Product Manufacturing Information）标注信息。输出每个标注的文本内容、类型及其关联的零件名称。

## 改动文件

| 文件 | 改动类型 | 行数 |
|------|---------|------|
| `pipeline/pmi_diag.py` | **新增** | ~200 行 |
| `pipeline.py` | 修改 | +35 行 |

## 架构

```
pipeline.py --pmi
  └─ _run_pmi(args)
       ├─ read_stp_with_doc(stp)          # 复用 STP 读取
       ├─ extract_pmi(doc)                 # pipeline/pmi_diag.py
       │    ├─ DimTolTool.GetDatumLabels()  → 遍历 Datum 标注
       │    ├─ DimTolTool.GetDimTolLabels() → 遍历 DimTol 标注
       │    ├─ NotesTool.GetNotes()         → 遍历 Note 标注
       │    └─ DimTolTool.GetRefShapeLabel() → 反查关联零件
       ├─ format_pmi_report(data)          # 格式化输出
       ├─ log(report)                      # → stdout（Electron 日志面板）
       └─ write pmi_report.txt             # → output_dir
```

## PMI 数据源

OCCT 的 `STEPCAFControl_Reader` 在开启 `SetGDTMode(True)` + `SetViewMode(True)` 后，会将 AP242 STEP 文件中的 PMI 数据导入到 XCAF 文档的三个工具中：

| 工具类 | 标注类型 | 文本获取方式 |
|--------|---------|------------|
| `XCAFDoc_DimTolTool` | Datum（基准） | `GetDatum()` → `name`, `description`, `identification` |
| `XCAFDoc_DimTolTool` | DimTol（尺寸/公差） | `GetDimTol()` → `kind`, `name`, `description` |
| `XCAFDoc_NotesTool` | Note（注释/气泡） | `NoteComment.Comment()` / `NoteBalloon.Comment()` |

每种标注均可通过 `GetRefShapeLabel()` 反查关联的零件 shape label，并通过 `GetLabelName()` 获取零件名称。

## 使用方式

### 生产环境

```bash
AutoModel.exe "E:\STEP\文件.stp" --pmi --output-dir .\output
```

### 开发环境

```bash
python pipeline.py "E:\STEP\文件.stp" --pmi --output-dir .\output
```

### 输出示例

```
=== PMI 标注诊断 ===
STP: E:\STEP\Gusto-grabcad.STEP
  读取 (102.8s), Root shapes: 1

[PMI] PMI 标注探测结果
[PMI] Datum: 42  |  DimTol: 156  |  Note: 23
------------------------------------------------------------
[PMI] Datum 标注 (42):
[PMI]   name='T01' desc='端子T01' id='' → NAUO1_S003, NAUO1_S004
[PMI]   name='T04' desc='' id='D4' → NAUO12_S001
[PMI]   name='T11' desc='' id='D11' → NAUO16_S004, NAUO16_S005
...
------------------------------------------------------------
[PMI] DimTol 标注 (156):
[PMI]   kind=5 name='FCF\wT01\w' desc='' → NAUO1_S003
...
------------------------------------------------------------
[PMI] Note 标注 (23):
[PMI]   [balloon] 'T01端子-左前' → NAUO1_S003
...
------------------------------------------------------------
  report: output\pmi_report.txt (12.3 KB)
```

## 输出文件

| 文件 | 位置 | 内容 |
|------|------|------|
| `pmi_report.txt` | `--output-dir` 目录 | 完整 PMI 报告（Markdown 兼容） |

## 设计约束

- **只读模式**：`--pmi` 只输出日志，不产生 glb/jpg 等二进制文件
- **异常安全**：任何 OCCT API 调用均被 try/except 包裹，单个标注失败不影响整体
- **UI 进度**：诊断结果通过 `print(msg, flush=True)` 输出，Electron 的 `stdout` 管道实时捕获
- **不依赖 BOM/XLSX**：`--pmi` 只需 STP 文件路径，无需其他参数

## 下一步

拿到 PMI 报告后，确认端子号（T01/T04/T11 等）出现在哪种标注类型中、以及它们关联的零件名称格式，据此设计"位置图（电路）"功能的标注逻辑。

## 版本历史

- **2026-07-08**: 初始实现，支持 Datum/DimTol/Note 三类 PMI 标注的探测和关联零件查询
