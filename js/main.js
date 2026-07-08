/**
 * 整车数模自动拆装方案系统 — Electron 桌面应用
 *
 * 单一场景架构：一个视口 + 一个渲染器 + 一个场景。
 * 四个 Tab 共享同一份模型数据（shared），各自维护独立的爆炸/树状态。
 */
 
import * as THREE from '../node_modules/three/build/three.module.js';
import { ModelLoader } from './model-loader.js';
import { SceneManager } from './scene-manager.js';
import { ExplosionView } from './explosion-view.js';
import { TreeView } from './tree-view.js';
import { Annotation } from './annotation.js';
import { ExportManager } from './export.js';
import { BodyLoader } from './body-loader.js';
import { AssemblyLoader } from './assembly-loader.js';

// ── DOM ───────────────────────────────────────────────────
const tabBtns = document.querySelectorAll('#tab-bar .tab');
const viewport = document.getElementById('viewport');
const panelBody = document.getElementById('panel-body');
const panelHeader = document.getElementById('panel-header');
const statusBar = document.getElementById('status-bar');

function getPipelineLog() {
  return document.getElementById('pipeline-log');
}

let activeTab = 0;
let pipelineMode = null;

// ── Shared singletons ─────────────────────────────────────
const sm = new SceneManager(viewport, { backgroundColor: 0xffffff, onRender: () => annot.draw() });
const modelLoader = new ModelLoader();
const bodyLoader = new BodyLoader();
const annot = new Annotation(sm.scene, sm.camera, viewport);
const exportMgr = new ExportManager(sm.renderer);

// ── Shared model data (one dataset for all three tabs) ────
const shared = {
  assembly: null,
  loaded: null,
  meshes: [],
  groups: [],
  fixedPartIds: new Set(),
  hierarchy: null,
  selectedNode: null,
  sourceStpPath: null,
  checkedPartIds: new Set(),
  checkedNodes: [],
  hiddenPartIds: new Set(),
  bomEntries: [],
  bomSourcePath: null,
  bomModelsDir: null,
  explosionCenter: null,
  compounds: [],
  bomCompounds: [],
};

// ── Per-tab state ─────────────────────────────────────────
const tabs = [
  { mode: 'compare', tree: null },
  { mode: 'clean', tree: null },
  { mode: 'position', tree: null },
  { mode: 'explosion', tree: null },
  { mode: 'serviceability', tree: null },
  { mode: 'manual', tree: null },
  { mode: 'help', tree: null },
];

const sharedExplo = new ExplosionView(sm.scene, sm.camera, sm.renderer.domElement, sm.controls);
sharedExplo.onStatus((msg) => { statusBar.textContent = msg; });
sharedExplo.onClearHighlight = () => { _clearHighlight(); statusBar.textContent = '就绪'; };

// ── Tab Switching ────────────────────────────────────────
tabBtns.forEach((btn) => {
  btn.addEventListener('click', () => switchTab(parseInt(btn.dataset.tab)));
});

function switchTab(idx) {
  if (idx === activeTab) return;

  sharedExplo.restoreAll();
  sharedExplo.disableManualMode();
  sharedExplo.hideThrustLines();

  activeTab = idx;
  tabBtns.forEach((b, i) => b.classList.toggle('active', i === idx));

  const titles = ['数模对比', '数模清洗', '位置图', '爆炸图', '拆装方案（可维修性）', '拆装方案（维修手册）', '帮助'];
  panelHeader.textContent = titles[idx];

  renderPanel(idx);
  statusBar.textContent = '就绪';
}

// ── Render Panel ─────────────────────────────────────────
function renderPanel(idx) {
  panelBody.innerHTML = '';
  switch (idx) {
    case 0: renderComparePanel(); break;
    case 1: renderCleanPanel(); break;
    case 2: renderPositionPanel(); break;
    case 3: renderExplosionPanel(); break;
    case 4: renderServiceabilityPanel(); break;
    case 5: renderManualPanel(); break;
    case 6: renderHelpPanel(); break;
  }
}

function _renderBomList() {
  const container = document.getElementById('bom-list');
  if (!container) return;
  container.innerHTML = '';

  if (!shared.assembly || !shared.assembly.parts) {
    container.textContent = '(无数据)';
    return;
  }

  const bomMap = new Map();
  for (const part of shared.assembly.parts) {
    if (part.bomSource) {
      const key = part.bomSource.code || part.bomSource.name;
      if (!bomMap.has(key)) {
        bomMap.set(key, {
          name: part.bomSource.name || key,
          code: part.bomSource.code || '',
          partIds: [],
        });
      }
      bomMap.get(key).partIds.push(part.id);
    }
  }

  if (bomMap.size === 0) {
    container.textContent = '(非BOM加载)';
    return;
  }

  for (const [key, info] of bomMap) {
    const totalParts = info.partIds.length;
    const visible = info.partIds.filter(id => !shared.hiddenPartIds.has(id)).length;
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;align-items:center;padding:2px 0;cursor:pointer;';
    row.title = info.name + ' (' + visible + '/' + totalParts + ' 可见)';

    const eye = document.createElement('span');
    eye.style.cssText = 'width:16px;text-align:center;font-size:10px;color:' + (visible > 0 ? '#1e90ff' : '#555') + ';';
    eye.textContent = visible > 0 ? '●' : '◌';

    const name = document.createElement('span');
    name.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-left:4px;';
    name.textContent = info.name || info.code;

    const badge = document.createElement('span');
    badge.style.cssText = 'font-size:9px;padding:1px 4px;border-radius:2px;background:#333;color:#aaa;margin-left:4px;';
    badge.textContent = visible + '/' + totalParts;

    row.appendChild(eye);
    row.appendChild(name);
    row.appendChild(badge);

    row.addEventListener('click', () => {
      const wasVisible = visible > 0;
      if (wasVisible) {
        for (const pid of info.partIds) shared.hiddenPartIds.add(pid);
      } else {
        for (const pid of info.partIds) shared.hiddenPartIds.delete(pid);
      }
      _applyVisibilityToScene();
      for (const t of tabs) { if (t.tree) t.tree.setHiddenPartIds(shared.hiddenPartIds); }
      _renderBomList();
      statusBar.textContent = wasVisible
        ? '已隐藏: ' + info.name
        : '已显示: ' + info.name;
    });

    container.appendChild(row);
  }
}

function renderComparePanel() {
  let h = '';
  h += '<div class="section-title">对比操作</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-compare-run">选择对比表格 → 开始对比</button>';
  h += '</div>';
  h += '<div class="section-title">管线进度</div>';
  h += '<div id="pipeline-log-placeholder" style="margin:4px 10px;padding:6px;background:#0a0a1a;border-radius:3px;font-family:Consolas,monospace;font-size:9px;color:#7ec8e3;max-height:150px;overflow-y:auto;"></div>';
  h += '<div class="section-title">对比结果</div>';
  h += '<div id="compare-result" style="margin:4px 10px;font-size:11px;color:#ccc;max-height:calc(100vh - 380px);overflow-y:auto;"></div>';
  panelBody.innerHTML = h;
  bindComparePanel();
}

function bindComparePanel() {
  document.getElementById('btn-compare-run')?.addEventListener('click', async () => {
    if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
    statusBar.textContent = '启动数模对比...';
    await window.electronAPI.runComparePipeline();
  });
  buildActiveTree();
}

function _renderCompareResult(payload) {
  const el = document.getElementById('compare-result');
  if (!el) return;

  const pairs = payload.pairs || [];
  const total = payload.total_pairs || pairs.length;
  const identical = payload.identical ?? 0;
  const minorDiff = payload.minor_diff ?? 0;
  const significantDiff = payload.significant_diff ?? 0;
  const failed = payload.failed ?? 0;

  let h = '';
  h += '<div style="padding:6px;margin-bottom:6px;background:#1a2a4a;border-radius:3px;">';
  h += '<span style="color:#7ec8e3;font-weight:bold;">对比汇总</span><br>';
  h += '总共 ' + total + ' 对 | ';
  h += '<span style="color:#2ecc71">一致 ' + identical + '</span> | ';
  h += '<span style="color:#f39c12">细微差异 ' + minorDiff + '</span> | ';
  h += '<span style="color:#e74c3c">明显不一致 ' + significantDiff + '</span>';
  if (failed > 0) h += ' | <span style="color:#888">失败 ' + failed + '</span>';
  h += '</div>';

  if (pairs.length === 0) {
    h += '<div style="color:#889;">无对比结果</div>';
    el.innerHTML = h;
    return;
  }

  for (const pair of pairs) {
    const cls = pair.classification || 'error';
    let badgeColor = '#888';
    if (cls === '一致') badgeColor = '#2ecc71';
    else if (cls === '细微差异') badgeColor = '#f39c12';
    else if (cls === '明显不一致') badgeColor = '#e74c3c';

    h += '<div style="margin-bottom:8px;padding:6px;background:#0d1b33;border-radius:3px;border-left:3px solid ' + badgeColor + ';">';
    h += '<div style="font-weight:bold;">';
    h += pair.code_a + ' ↔ ' + pair.code_b;
    h += ' <span style="display:inline-block;padding:1px 6px;border-radius:2px;font-size:10px;background:' + badgeColor + ';color:#fff;">' + (cls || '?') + '</span>';
    h += '</div>';

    if (pair.error) {
      h += '<div style="color:#e74c3c;margin-top:2px;">错误: ' + (pair.message || pair.error) + '</div>';
    } else {
      const g = pair.geometric || {};
      const simPct = (g.similarity != null ? (g.similarity * 100).toFixed(1) : '?') + '%';
      h += '<div style="margin-top:2px;font-size:10px;">';
      h += '相似度: <b>' + simPct + '</b> | ';
      h += '体积A: ' + (g.volume_a != null ? g.volume_a.toFixed(0) : '?') + ' | ';
      h += '体积B: ' + (g.volume_b != null ? g.volume_b.toFixed(0) : '?') + ' | ';
      h += '交集体积: ' + (g.intersection_volume != null ? g.intersection_volume.toFixed(0) : '?');
      h += '</div>';
      const s = pair.structural || {};
      if (s.part_count_a != null && s.part_count_b != null) {
        h += '<div style="margin-top:2px;font-size:10px;color:#889;">';
        h += '零件: A=' + s.part_count_a + ' B=' + s.part_count_b;
        h += '</div>';
      }
    }
    h += '</div>';
  }

  el.innerHTML = h;
}

function renderPositionPanel() {
  let h = '';
  h += '<div class="section-title">数据加载</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-load-bom">批量生成位置图</button>';
  h += '</div>';
  h += '<div class="section-title">车壳选择</div>';
  h += '<select class="sel" id="sel-body">';
  for (const b of bodyLoader.bodies) {
    h += '<option value="' + b.name + '">' + b.name + '</option>';
  }
  h += '</select>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-import-body">导入新壳</button>';
  h += '</div>';
  h += '<div class="section-title">可见性</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-show-all">全部显示</button>';
  h += '<button class="btn btn-outline" id="btn-hide-all">全部隐藏</button>';
  h += '<button class="btn btn-outline" id="btn-show-selected">仅显示选中</button>';
  h += '</div>';
  h += '<div class="section-title">BOM 条目</div>';
  h += '<div id="bom-list" style="margin:4px 12px;max-height:120px;overflow-y:auto;font-size:11px;color:#ccc;"></div>';
  h += '<div class="section-title">标注导出</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-annot-show">显示标注</button>';
  h += '<button class="btn btn-outline" id="btn-annot-hide">清除标注</button>';
  h += '<button class="btn btn-outline" id="btn-export">导出 PNG</button>';
  h += '<button class="btn btn-outline" id="btn-export-svg-position">导出 SVG</button>';
  h += '</div>';
  h += '<div class="section-title">标注管理</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-create-compound">勾选部件 → 生成标注</button>';
  h += '<button class="btn btn-outline" id="btn-clear-compounds">清空标注</button>';
  h += '</div>';
  h += '<div id="compound-preview" style="margin:4px 10px;font-size:10px;color:#889;min-height:18px;">未创建标注</div>';
  panelBody.innerHTML = h;
  bindPositionPanel();
}

function renderExplosionPanel() {
  let h = '';
  h += '<div class="section-title">编组管理</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-create-compound">勾选部件 → 创建编组</button>';
  h += '<button class="btn btn-outline" id="btn-clear-compounds">清空编组</button>';
  h += '</div>';
  h += '<div id="compound-preview" style="margin:4px 10px;font-size:10px;color:#889;min-height:18px;">未创建编组</div>';
  h += '<div class="section-title">爆炸中心</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-set-center">选中爆炸中心</button>';
  h += '<button class="btn btn-outline" id="btn-clear-center">清除中心</button>';
  h += '</div>';
  h += '<div id="center-display" style="margin:4px 12px;font-size:11px;color:#7ec8e3;">未设置 (使用几何重心)</div>';
  h += '<div class="section-title">爆炸控制</div>';
  h += '<div class="slider-row"><span>距离</span><input type="range" id="slider-dist" min="10" max="2000" value="150" step="5"><span id="val-dist">150</span>mm</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-explode-instant">爆炸</button>';
  h += '<button class="btn btn-outline" id="btn-reset">复位</button></div>';
  h += '<div class="section-title">手动移动</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-manual-on">开启拖拽</button>';
  h += '<button class="btn btn-outline" id="btn-manual-off">关闭拖拽</button></div>';
  h += '<div class="section-title">标注导出</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-annot-show">显示标注</button>';
  h += '<button class="btn btn-outline" id="btn-annot-hide">清除标注</button>';
  h += '<button class="btn btn-outline" id="btn-thrust">推力线</button>';
  h += '<button class="btn btn-outline" id="btn-export">导出 PNG</button>';
  h += '<button class="btn btn-outline" id="btn-export-svg-explosion">导出 SVG</button>';
  h += '</div>';
  panelBody.innerHTML = h;
  bindExplosionPanel();
}

function renderServiceabilityPanel() {
  let h = '';
  h += '<div class="section-title">管线进度</div>';
  h += '<div id="pipeline-log-placeholder" style="margin:4px 10px;padding:6px;background:#0a0a1a;border-radius:3px;font-family:Consolas,monospace;font-size:9px;color:#7ec8e3;max-height:120px;overflow-y:auto;"></div>';
  h += '<div class="section-title">编组管理</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-create-compound">勾选部件 → 创建编组</button>';
  h += '<button class="btn btn-outline" id="btn-clear-compounds">清空编组</button>';
  h += '</div>';
  h += '<div id="compound-preview" style="margin:4px 10px;font-size:10px;color:#889;min-height:18px;">未创建编组</div>';
  h += '<div class="section-title">依赖链分析</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-pipeline-chain">选中目标 → 分析拆卸链</button>';
  h += '<span id="sel-node-display" style="padding:5px;color:#7ec8e3;font-size:11px;">未选中</span>';
  h += '</div>';
  h += '<div class="section-title">全量拆装</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-pipeline-node">选中节点 → 生成拆装方案</button>';
  h += '</div>';
  h += '<div class="section-title">拆卸演示</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-disassemble">逐件拆卸演示</button>';
  h += '<button class="btn btn-outline" id="btn-step">单步拆卸</button>';
  h += '<button class="btn btn-outline" id="btn-restore">复位全部</button>';
  h += '</div>';
  h += '<div class="section-title">导出</div>';
  h += '<div class="btn-group"><button class="btn btn-outline" id="btn-export">导出 PNG</button></div>';
  h += '<div class="section-title">依赖链演示</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-chain-demo">AI最佳拆装路径</button>';
  h += '<button class="btn btn-outline" id="btn-chain-reset">复位</button>';
  h += '</div>';
  panelBody.innerHTML = h;
  bindServiceabilityPanel();
}

function renderManualPanel() {
  let h = '';
  h += '<div class="section-title">管线进度</div>';
  h += '<div id="pipeline-log-placeholder" style="margin:4px 10px;padding:6px;background:#0a0a1a;border-radius:3px;font-family:Consolas,monospace;font-size:9px;color:#7ec8e3;max-height:120px;overflow-y:auto;"></div>';
  h += '<div class="section-title">数据加载</div>';
  h += '<div class="btn-group"><button class="btn btn-pri" id="btn-load-bom-manual">加载 BOM (多文件)</button></div>';
  h += '<div class="section-title">依赖链分析</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-pipeline-chain-manual">选中目标 → 分析拆卸链</button>';
  h += '<span id="sel-node-display-manual" style="padding:5px;color:#7ec8e3;font-size:11px;">未选中</span>';
  h += '</div>';
  h += '<div class="section-title">全量拆装</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-pipeline-node-manual">选中节点 → 生成拆装方案</button>';
  h += '</div>';
  h += '<div class="section-title">拆卸演示</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-disassemble-manual">逐件拆卸演示</button>';
  h += '<button class="btn btn-outline" id="btn-step-manual">单步拆卸</button>';
  h += '<button class="btn btn-outline" id="btn-restore-manual">复位全部</button>';
  h += '</div>';
  h += '<div class="section-title">依赖链演示</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-chain-demo-manual">AI最佳拆装路径</button>';
  h += '<button class="btn btn-outline" id="btn-chain-reset-manual">复位</button>';
  h += '</div>';
  panelBody.innerHTML = h;
  bindManualPanel();
}

function renderCleanPanel() {
  let h = '';
  h += '<div class="section-title">管线进度</div>';
  h += '<div id="pipeline-log-placeholder" style="margin:4px 10px;padding:6px;background:#0a0a1a;border-radius:3px;font-family:Consolas,monospace;font-size:9px;color:#7ec8e3;max-height:120px;overflow-y:auto;"></div>';
  h += '<div class="section-title">数据加载</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-clean-run">加载 STP + BOM 并清洗</button>';
  h += '</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-clean-load-result">加载清洗结果 JSON</button>';
  h += '</div>';
  h += '<div class="section-title">清洗步骤</div>';
  h += '<div style="margin:4px 12px;font-size:11px;color:#889;">';
  h += '<ol style="padding-left:18px;line-height:1.6">';
  h += '<li>J列名称匹配 — 保留与BOM J列相关的零件</li>';
  h += '<li>干涉检查 — 保留不干涉匹配零件的其他件</li>';
  h += '<li>形状去重 — 移除位置/形状重复的堆叠件</li>';
  h += '<li>清除其余 — 输出清洗后结果</li>';
  h += '</ol></div>';
  h += '<div class="section-title">导出</div>';
  h += '<div class="btn-group"><button class="btn btn-outline" id="btn-export-clean">导出 PNG</button></div>';
  panelBody.innerHTML = h;
  bindCleanPanel();
}

const HELP_FEATURES = [
  {
    folder: '01-compare',
    title: '数模对比',
    desc: '加载对比Excel表格(Sheet3, A列/B列为模型代号)，自动完成两个STP数模的整体几何对比。算法: 质心自动对齐 → 布尔交/差集体积计算 → 分类为一致/细微差异/明显不一致。',
  },
  {
    folder: '02-clean',
    title: '数模清洗',
    desc: '按BOM表格J列匹配零件 + 干涉检查 + 去重清洗模型。菜单 文件 → 数模清洗 (STP + BOM)。',
  },
  {
    folder: '03-position',
    title: '位置图',
    desc: '以原始装配位置查看全部零件。左侧结构树点击零件可高亮聚焦。右侧面板可切换车壳叠加显示。操作: 菜单 文件 → 加载单个 STEP 文件 (Ctrl+O) 或 通过表格加载多个 STEP 文件 (Ctrl+B)。',
  },
  {
    folder: '04-explosion',
    title: '爆炸图',
    desc: '查看零件的爆炸分解视图，支持手动调整爆炸程度和位置。通过右侧滑块控制爆炸程度(0%-100%)，或点击"一键爆炸"立即展开。支持 TransformControls 手动拖拽调整单个零件位置，可选显示推力线标注爆炸方向。',
  },
  {
    folder: '05-serviceability',
    title: '拆装方案（可维修性）',
    desc: '为整个装配体生成完整的分阶段拆卸序列。菜单 管线 → 生成拆装方案 (Ctrl+G) 触发8步分析管线。产出: 分阶段拆卸顺序列表 + 步骤动画演示。编组管理: 勾选零件后可根据标注生成编组。',
  },
  {
    folder: '06-manual',
    title: '拆装方案（维修手册）',
    desc: '针对指定目标零件，计算"要拆这个零件必须先拆哪些"的完整依赖链条。算法: 从26个候选方向中选出最优8个 → 并行碰撞检测 → 光束搜索(K=4)递归模拟总拆卸成本 → 选择最优方向。产出: 依赖链概要 + 方向对比表 + 逐阶段拆卸顺序 + AI最佳拆装路径动画。',
  },
];

let _helpFeatureIdx = 0;
let _helpImageIdx = 0;
let _helpImageList = [];

function _showHelpCarousel(startFeatureIndex) {
  const existing = document.getElementById('help-carousel-overlay');
  if (existing) existing.remove();

  _helpFeatureIdx = (startFeatureIndex >= 0 && startFeatureIndex < HELP_FEATURES.length) ? startFeatureIndex : 0;
  _helpImageIdx = 0;
  _helpImageList = [];

  const overlay = document.createElement('div');
  overlay.id = 'help-carousel-overlay';
  overlay.tabIndex = 0;
  overlay.style.cssText =
    'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);' +
    'z-index:9999;display:flex;align-items:center;justify-content:center;outline:none;';
  overlay.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); _helpShift(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); _helpShift(-1); }
    else if (e.key === 'Escape') { e.preventDefault(); overlay.remove(); }
  });

  const box = document.createElement('div');
  box.style.cssText =
    'background:#1a1a2e;color:#e0e0e0;border:1px solid #3a3a5a;border-radius:8px;' +
    'padding:16px 20px 12px;max-width:1230px;min-width:960px;' +
    'box-shadow:0 8px 32px rgba(0,0,0,0.7);display:flex;flex-direction:column;' +
    'max-height:92vh;';
  box.addEventListener('click', (e) => e.stopPropagation());

  box.innerHTML =
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">' +
    '<h3 style="margin:0;font-size:14px;color:#7ec8e3;">帮助</h3>' +
    '<button id="help-carousel-close" style="background:none;border:none;color:#999;font-size:18px;' +
    'cursor:pointer;padding:0 4px;line-height:1;" title="关闭">&times;</button>' +
    '</div>' +
    '<div id="help-feature-tabs" style="display:flex;gap:2px;margin-bottom:10px;flex-wrap:wrap;"></div>' +
    '<div style="display:flex;align-items:center;gap:8px;flex:1;min-height:0;">' +
    '<button id="help-carousel-prev" style="background:rgba(255,255,255,0.08);border:1px solid #3a3a5a;' +
    'color:#ccc;font-size:24px;cursor:pointer;border-radius:4px;padding:10px 14px;' +
    'flex-shrink:0;user-select:none;" title="上一张">&lt;</button>' +
    '<div style="flex:1;display:flex;align-items:center;justify-content:center;' +
    'background:#0a0a1a;border-radius:4px;overflow:hidden;min-height:300px;max-height:55vh;">' +
    '<img id="help-carousel-img" style="max-width:100%;max-height:55vh;object-fit:contain;display:none;" />' +
    '<span id="help-carousel-placeholder" style="color:#666;font-size:13px;">加载中...</span>' +
    '</div>' +
    '<button id="help-carousel-next" style="background:rgba(255,255,255,0.08);border:1px solid #3a3a5a;' +
    'color:#ccc;font-size:24px;cursor:pointer;border-radius:4px;padding:10px 14px;' +
    'flex-shrink:0;user-select:none;" title="下一张">&gt;</button>' +
    '</div>' +
    '<p id="help-carousel-desc" style="margin:10px 0 8px;font-size:12px;line-height:1.6;color:#bbb;text-align:center;"></p>' +
    '<div style="display:flex;align-items:center;justify-content:space-between;">' +
    '<div id="help-carousel-dots" style="display:flex;gap:6px;"></div>' +
    '<span id="help-carousel-counter" style="font-size:11px;color:#888;"></span>' +
    '</div>';

  overlay.appendChild(box);
  document.body.appendChild(overlay);

  document.getElementById('help-carousel-close').onclick = () => overlay.remove();
  document.getElementById('help-carousel-prev').onclick = () => _helpShift(-1);
  document.getElementById('help-carousel-next').onclick = () => _helpShift(1);
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  _helpRender();
  overlay.focus();
}

async function _helpRender() {
  const feature = HELP_FEATURES[_helpFeatureIdx];

  const tabs = document.getElementById('help-feature-tabs');
  tabs.innerHTML = '';
  for (let i = 0; i < HELP_FEATURES.length; i++) {
    const tab = document.createElement('button');
    const active = i === _helpFeatureIdx;
    tab.textContent = HELP_FEATURES[i].title;
    tab.style.cssText =
      'padding:5px 10px;font-size:11px;border:1px solid ' + (active ? '#7ec8e3' : '#3a3a5a') + ';' +
      'background:' + (active ? '#2a5a8c' : 'transparent') + ';color:' + (active ? '#fff' : '#aaa') + ';' +
      'border-radius:3px;cursor:pointer;white-space:nowrap;';
    (function (idx) { tab.onclick = () => _helpSwitchFeature(idx); })(i);
    tabs.appendChild(tab);
  }

  document.getElementById('help-carousel-desc').textContent = feature.desc;

  if (window.electronAPI && window.electronAPI.listHelpImages) {
    _helpImageList = await window.electronAPI.listHelpImages(feature.folder);
  } else {
    _helpImageList = [];
  }

  if (_helpImageIdx >= _helpImageList.length) _helpImageIdx = Math.max(0, _helpImageList.length - 1);

  const hasImages = _helpImageList.length > 0;
  document.getElementById('help-carousel-counter').textContent = hasImages
    ? (_helpImageIdx + 1) + ' / ' + _helpImageList.length
    : '无图片';
  document.getElementById('help-carousel-prev').style.visibility =
    (hasImages && _helpImageIdx > 0) ? 'visible' : 'hidden';
  document.getElementById('help-carousel-next').style.visibility =
    (hasImages && _helpImageIdx < _helpImageList.length - 1) ? 'visible' : 'hidden';

  const dots = document.getElementById('help-carousel-dots');
  dots.innerHTML = '';
  for (let i = 0; i < _helpImageList.length; i++) {
    const dot = document.createElement('span');
    dot.style.cssText =
      'width:8px;height:8px;border-radius:50%;background:' + (i === _helpImageIdx ? '#7ec8e3' : '#555') + ';' +
      'display:inline-block;cursor:pointer;';
    (function (idx) { dot.onclick = () => { _helpImageIdx = idx; _helpRender(); }; })(i);
    dots.appendChild(dot);
  }

  const img = document.getElementById('help-carousel-img');
  const placeholder = document.getElementById('help-carousel-placeholder');
  img.style.display = 'none';
  placeholder.style.display = 'inline';

  if (hasImages && window.electronAPI && window.electronAPI.readHelpImage) {
    const dataUrl = await window.electronAPI.readHelpImage(feature.folder, _helpImageList[_helpImageIdx]);
    if (dataUrl) {
      img.src = dataUrl;
      img.style.display = 'inline';
      placeholder.style.display = 'none';
    } else {
      placeholder.textContent = '图片未找到: ' + feature.folder + '/' + _helpImageList[_helpImageIdx];
    }
  } else if (!hasImages) {
    placeholder.textContent = '该功能暂无示意图';
  } else {
    placeholder.textContent = '图片加载不可用（非 Electron 环境）';
  }
}

function _helpShift(delta) {
  const nextIdx = _helpImageIdx + delta;
  if (nextIdx < 0 || nextIdx >= _helpImageList.length) return;
  _helpImageIdx = nextIdx;
  _helpRender();
}

function _helpSwitchFeature(idx) {
  if (idx === _helpFeatureIdx) return;
  _helpFeatureIdx = idx;
  _helpImageIdx = 0;
  _helpImageList = [];
  _helpRender();
}

function renderHelpPanel() {
  panelBody.innerHTML = '';
  _showHelpCarousel(0);
}

function _bindHelpPanel() {
}

function bindCleanPanel() {
  document.getElementById('btn-clean-run')?.addEventListener('click', async () => {
    if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
    statusBar.textContent = '启动数模清洗...';
    await window.electronAPI.runCleanPipeline();
  });
  document.getElementById('btn-clean-load-result')?.addEventListener('click', loadAssembly);
  document.getElementById('btn-export-clean')?.addEventListener('click', _exportSimple);
  buildActiveTree();
}

// ── Panel Event Binders ──────────────────────────────────

let _activeChainPayload = null;
let _preDemoGroups = null;
let _inChainDemo = false;

function _bindChainDemoButtons() {
  document.getElementById('btn-chain-demo')?.addEventListener('click', () => {
    if (!_activeChainPayload) {
      _showModal('未执行分析',
        '请先运行依赖链分析：<br><br>' +
        '在<b>结构树</b>中选择目标零件 → 点击<b>"选中目标 → 分析拆卸链"</b>');
      return;
    }
    if (_inChainDemo) {
      statusBar.textContent = '依赖链演示进行中，请先复位';
      return;
    }
    _startChainDemo(_activeChainPayload);
  });
  document.getElementById('btn-chain-reset')?.addEventListener('click', () => {
    _endChainDemo();
  });
}

function _startChainDemo(payload) {
  if (!_inChainDemo) {
    _preDemoGroups = shared.groups ? [...shared.groups] : null;
  }

  shared.fixedPartIds.clear();
  sharedExplo.setFixedPartIds(new Set());

  // Build nodeId -> leaf partId lookup from hierarchy
  const nodeToLeafIds = new Map();
  function _walkHierarchy(node) {
    nodeToLeafIds.set(node.id, node.partIds || []);
    for (const c of node.children || []) _walkHierarchy(c);
  }
  for (const root of (shared.hierarchy || [])) _walkHierarchy(root);

  const chainPartIds = new Set();
  for (const stg of payload.chain || []) {
    for (const p of stg.parts || []) {
      const resolved = nodeToLeafIds.get(p);
      if (resolved && resolved.length > 1) {
        for (const id of resolved) chainPartIds.add(id);
      } else {
        chainPartIds.add(p);
      }
    }
  }

  const stageByPart = {};
  for (let i = 0; i < (payload.chain || []).length; i++) {
    for (const p of payload.chain[i].parts || []) {
      const resolved = nodeToLeafIds.get(p);
      if (resolved && resolved.length > 1) {
        for (const id of resolved) stageByPart[id] = i + 1;
      } else {
        stageByPart[p] = i + 1;
      }
    }
  }

  const chainGroups = [];
  for (const g of (shared.groups || [])) {
    const relevant = g.meshes.filter(m => chainPartIds.has(m.userData.partId));
    if (relevant.length === 0) continue;

    let stage = 1;
    for (const m of relevant) {
      if (stageByPart[m.userData.partId]) {
        stage = stageByPart[m.userData.partId];
        break;
      }
    }

    chainGroups.push({
      id: g.id,
      name: g.name,
      meshes: relevant,
      direction: payload.chosen_direction || g.direction,
      distanceMultiplier: g.distanceMultiplier || 1.0,
      stage: stage,
    });
  }

  if (chainGroups.length === 0) {
    statusBar.textContent = '依赖链零件在场景中未加载';
    return;
  }

  sharedExplo.loadAssemblyGroups(chainGroups);
  _inChainDemo = true;
  statusBar.textContent = '依赖链演示: ' + chainPartIds.size + ' 个零件';

  setTimeout(() => {
    sharedExplo.disassembleSequential(800);
  }, 200);
}

function _endChainDemo() {
  sharedExplo.restoreAll();
  if (_preDemoGroups) {
    sharedExplo.loadAssemblyGroups(_preDemoGroups);
    _preDemoGroups = null;
  }
  shared.fixedPartIds.clear();
  sharedExplo.setFixedPartIds(new Set());
  _inChainDemo = false;
  statusBar.textContent = '已恢复整车视图';
}

function _bindDisassemblyButtons() {
  document.getElementById('btn-disassemble')?.addEventListener('click', () => {
    sharedExplo.disassembleSequential(800);
    statusBar.textContent = '逐件拆卸演示中...';
  });
  document.getElementById('btn-step')?.addEventListener('click', () => {
    sharedExplo.disassembleOneStep(600);
    statusBar.textContent = '单步拆卸';
  });
  document.getElementById('btn-restore')?.addEventListener('click', () => {
    sharedExplo.restoreAll();
    statusBar.textContent = '已复位全部零件';
  });
}

function bindPositionPanel() {
  document.getElementById('btn-load-bom')?.addEventListener('click', async () => {
    if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
    await _batchPositionCapture();
  });

  document.getElementById('sel-body')?.addEventListener('change', async (e) => {
    await bodyLoader.switchBody(e.target.selectedIndex, sm.scene);
  });
  document.getElementById('btn-import-body')?.addEventListener('click', async () => {
    if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
    statusBar.textContent = '导入车壳中...';
    const result = await window.electronAPI.importBody();
    if (result && result.ok) {
      await bodyLoader.reloadBodies();
      const sel = document.getElementById('sel-body');
      if (sel) {
        sel.innerHTML = '';
        for (const b of bodyLoader.bodies) {
          const opt = document.createElement('option');
          opt.value = b.name;
          opt.textContent = b.name;
          sel.appendChild(opt);
        }
        sel.value = result.name;
      }
      statusBar.textContent = '车壳已导入: ' + result.name;
    } else {
      statusBar.textContent = '导入车壳失败';
    }
  });

  document.getElementById('btn-show-all')?.addEventListener('click', () => {
    shared.hiddenPartIds.clear();
    _applyVisibilityToScene();
    for (const t of tabs) { if (t.tree) t.tree.setHiddenPartIds(new Set()); }
    statusBar.textContent = '已全部显示';
  });

  document.getElementById('btn-hide-all')?.addEventListener('click', () => {
    for (const m of shared.meshes) shared.hiddenPartIds.add(m.userData.partId);
    _applyVisibilityToScene();
    for (const t of tabs) { if (t.tree) t.tree.setHiddenPartIds(shared.hiddenPartIds); }
    statusBar.textContent = '已全部隐藏';
  });

  document.getElementById('btn-show-selected')?.addEventListener('click', () => {
    const t = tabs[activeTab];
    if (!t.tree) { statusBar.textContent = '请先加载数据'; return; }
    t.tree.showOnlySelected();
    shared.hiddenPartIds = t.tree.getHiddenPartIds();
    _applyVisibilityToScene();
    statusBar.textContent = '仅显示选中: ' + shared.hiddenPartIds.size + ' 隐藏';
  });

  document.getElementById('btn-annot-show')?.addEventListener('click', () => {
    if (shared.assembly) annot.setParts(shared.assembly.parts, null, shared.compounds);
    annot.setHiddenPartIds(shared.hiddenPartIds);
    annot.show();
  });

  document.getElementById('btn-annot-hide')?.addEventListener('click', () => annot.clear());
  document.getElementById('btn-export')?.addEventListener('click', _exportAnnotated);
  document.getElementById('btn-export-svg-position')?.addEventListener('click', _exportSVG);
  _bindChainDemoButtons();
  document.getElementById('btn-create-compound')?.addEventListener('click', _createCompoundFromChecked);
  document.getElementById('btn-clear-compounds')?.addEventListener('click', _clearAllCompounds);
  _updateCompoundPreview();
  buildActiveTree();
  _renderBomList();
}

function _updateCenterDisplay() {
  const el = document.getElementById('center-display');
  if (!el) return;
  if (shared.explosionCenter) {
    el.textContent = '当前中心: ' + shared.explosionCenter;
    el.style.color = '#ffd24a';
  } else {
    el.textContent = '未设置 (使用几何重心)';
    el.style.color = '#7ec8e3';
  }
}

function _buildHierarchyLeafMap() {
  const map = new Map();
  function walk(node) {
    map.set(node.id, node.partIds || []);
    for (const c of node.children || []) walk(c);
  }
  for (const root of (shared.hierarchy || [])) walk(root);
  return map;
}

function bindExplosionPanel() {
  document.getElementById('btn-set-center')?.addEventListener('click', () => {
    const sel = shared.selectedNode
             || (tabs[activeTab].tree ? tabs[activeTab].tree.getCheckedNodeId() : null);
    if (!sel) {
      _showModal('未选择节点',
        '请先在<b>结构树</b>中点击或勾选一个零件 / 子装配节点，再点"选中爆炸中心"。');
      return;
    }
    shared.explosionCenter = sel;
    _updateCenterDisplay();
    statusBar.textContent = '已设置爆炸中心: ' + sel;
    if (sharedExplo.isExploded) {
      sharedExplo._lastExplosionCenter = sharedExplo.findCenterPoint(
        sel, _buildHierarchyLeafMap());
      sharedExplo.resetPositions();
      if (sharedExplo._lastCompounds != null) {
        sharedExplo._doRadialExplode(
          sharedExplo._lastExplosionCenter,
          sharedExplo.explosionDistance,
          sharedExplo._lastCompounds);
      }
      sharedExplo.isExploded = true;
    }
  });

  document.getElementById('btn-clear-center')?.addEventListener('click', () => {
    shared.explosionCenter = null;
    _updateCenterDisplay();
    statusBar.textContent = '已清除爆炸中心 (将使用几何重心)';
    if (sharedExplo.isExploded) {
      sharedExplo._lastExplosionCenter = sharedExplo.findCenterPoint(null, _buildHierarchyLeafMap());
      sharedExplo.resetPositions();
      if (sharedExplo._lastCompounds != null) {
        sharedExplo._doRadialExplode(
          sharedExplo._lastExplosionCenter,
          sharedExplo.explosionDistance,
          sharedExplo._lastCompounds);
      }
      sharedExplo.isExploded = true;
    }
  });

  document.getElementById('btn-create-compound')?.addEventListener('click', _createCompoundFromChecked);
  document.getElementById('btn-clear-compounds')?.addEventListener('click', _clearAllCompounds);

  const slider = document.getElementById('slider-dist');
  const val = document.getElementById('val-dist');
  slider?.addEventListener('input', () => {
    const v = parseInt(slider.value);
    val.textContent = v;
    sharedExplo.setExplosionDistance(v);
  });
  document.getElementById('btn-explode-instant')?.addEventListener('click', () => {
    const leafMap = _buildHierarchyLeafMap();
    const center = sharedExplo.findCenterPoint(shared.explosionCenter, leafMap);
    sharedExplo.radialExplodeInstant(center, sharedExplo.explosionDistance, shared.compounds);
  });
  document.getElementById('btn-reset')?.addEventListener('click', () => { sharedExplo.resetPositions(); sharedExplo.hideThrustLines(); });
  document.getElementById('btn-manual-on')?.addEventListener('click', () => sharedExplo.enableManualMode());
  document.getElementById('btn-manual-off')?.addEventListener('click', () => sharedExplo.disableManualMode());
  document.getElementById('btn-annot-show')?.addEventListener('click', () => {
    if (shared.assembly) annot.setParts(shared.assembly.parts, null, shared.compounds);
    annot.setHiddenPartIds(shared.hiddenPartIds);
    annot.show();
  });
  document.getElementById('btn-annot-hide')?.addEventListener('click', () => annot.clear());
  document.getElementById('btn-thrust')?.addEventListener('click', () => sharedExplo.toggleThrustLines());
  document.getElementById('btn-export')?.addEventListener('click', _exportAnnotated);
  document.getElementById('btn-export-svg-explosion')?.addEventListener('click', _exportSVG);
  _bindChainDemoButtons();
  if (shared.groups && shared.groups.length > 0) {
    sharedExplo.loadAssemblyGroups(shared.groups);
  }
  buildActiveTree();
  _updateCenterDisplay();
  _updateCompoundPreview();
}

function bindServiceabilityPanel() {
  document.getElementById('btn-pipeline-chain')?.addEventListener('click', async () => {
    if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
    const targetPart = _resolveTargetPart();
    if (!targetPart) { _showTargetHint(); return; }
    if (!shared.sourceStpPath) {
      _showModal('无可用数据', '请先通过 <b>Ctrl+I</b> 导入单文件 STP');
      return;
    }
    statusBar.textContent = '分析拆卸依赖链: ' + targetPart + '...';
    const compoundsJson = JSON.stringify(shared.compounds || []);
    await window.electronAPI.runSinglePipelineChain(
      shared.sourceStpPath, targetPart, compoundsJson);
  });
  document.getElementById('btn-export')?.addEventListener('click', _exportSimple);
  document.getElementById('btn-pipeline-node')?.addEventListener('click', async () => {
    if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
    const nodeName = shared.checkedPartIds.size > 0
      ? (tabs[activeTab].tree ? tabs[activeTab].tree.getCheckedNodeId() : null)
      : shared.selectedNode;
    if (!nodeName) { statusBar.textContent = '请先在结构树中勾选或选择一个节点'; return; }
    if (!shared.sourceStpPath) { statusBar.textContent = '请先导入 STP 预览 (Ctrl+I)'; return; }
    statusBar.textContent = '启动管线 (节点: ' + nodeName + ')...';
    const compoundsJson = JSON.stringify(shared.compounds || []);
    await window.electronAPI.runPipelineForNodeCached(
      shared.sourceStpPath, nodeName, compoundsJson);
  });
  document.getElementById('btn-create-compound')?.addEventListener('click', _createCompoundFromChecked);
  document.getElementById('btn-clear-compounds')?.addEventListener('click', _clearAllCompounds);
  _bindChainDemoButtons();
  _bindDisassemblyButtons();
  buildActiveTree();
  _updateCompoundPreview();
  const display = document.getElementById('sel-node-display');
  if (display) display.textContent = shared.selectedNode || '未选中';
}

function bindManualPanel() {
  document.getElementById('btn-load-bom-manual')?.addEventListener('click', async () => {
    if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
    statusBar.textContent = '加载 BOM...';
    await window.electronAPI.runBomPreviewPipeline();
  });
  document.getElementById('btn-pipeline-chain-manual')?.addEventListener('click', async () => {
    if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
    const targetPart = _resolveTargetPart();
    if (!targetPart) { _showTargetHint(); return; }
    if (!shared.bomSourcePath) {
      _showModal('无可用数据', '请先通过 <b>加载 BOM</b> 导入多文件 BOM');
      return;
    }
    statusBar.textContent = '分析拆卸依赖链: ' + targetPart + '...';
    await window.electronAPI.runBomFullPipelineCached(
      shared.bomSourcePath, shared.bomModelsDir, targetPart);
  });
  document.getElementById('btn-pipeline-node-manual')?.addEventListener('click', async () => {
    if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
    const nodeName = shared.checkedPartIds.size > 0
      ? (tabs[activeTab].tree ? tabs[activeTab].tree.getCheckedNodeId() : null)
      : shared.selectedNode;
    if (!nodeName) { statusBar.textContent = '请先在结构树中勾选或选择一个节点'; return; }
    if (!shared.bomSourcePath) { statusBar.textContent = '请先加载 BOM'; return; }
    statusBar.textContent = '启动管线 (BOM节点: ' + nodeName + ')...';
    await window.electronAPI.runBomFullPipelineCached(
      shared.bomSourcePath, shared.bomModelsDir, nodeName);
  });
  const ids = ['btn-chain-demo', 'btn-chain-reset', 'btn-disassemble', 'btn-step', 'btn-restore'];
  for (const baseId of ids) {
    const btn = document.getElementById(baseId + '-manual');
    if (btn) {
      const orig = document.getElementById(baseId);
      if (orig) btn.addEventListener('click', () => orig.click());
    }
  }
  buildActiveTree();
  const display = document.getElementById('sel-node-display-manual');
  if (display) display.textContent = shared.selectedNode || '未选中';
}

// ── Compound management ─────────────────────────────────

function _createCompoundFromChecked() {
  const tree = tabs[activeTab].tree;
  if (!tree) { statusBar.textContent = '请先加载装配数据'; return; }
  const checkedIds = Array.from(shared.checkedPartIds || []);
  if (checkedIds.length === 0) {
    statusBar.textContent = '请先在结构树中勾选要编组的零件，或按住 Ctrl 点击多个零件';
    return;
  }
  const name = '组_' + (shared.compounds.length + 1);
  const compound = { name, members: [...checkedIds] };
  shared.compounds.push(compound);
  tree.addCompound(name, checkedIds, _randColor());
  tree.clearChecked();
  _updateCompoundPreview();
  statusBar.textContent = '已创建编组: ' + name + ' (' + checkedIds.length + ' 件)';
}

function _clearAllCompounds() {
  shared.compounds = [];
  const tree = tabs[activeTab].tree;
  if (tree) tree.clearCompounds();
  _updateCompoundPreview();
  statusBar.textContent = '已清空所有编组';
}

function _updateCompoundPreview() {
  const el = document.getElementById('compound-preview');
  if (!el) return;
  if (shared.compounds.length === 0) {
    el.textContent = '未创建编组';
  } else {
    const items = shared.compounds.map(c => c.name + '(' + c.members.length + '件)').join(', ');
    el.textContent = '编组: ' + items;
  }
}

let _compoundColorIdx = 0;
function _randColor() {
  const colors = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#e67e22', '#34495e'];
  return colors[_compoundColorIdx++ % colors.length];
}

function _restoreCompoundsToTree() {
  const tree = tabs[activeTab].tree;
  if (!tree || !shared.compounds || shared.compounds.length === 0) return;
  tree.clearCompounds();
  for (const c of shared.compounds) {
    tree.addCompound(c.name, c.members, _randColor());
  }
  _updateCompoundPreview();
}

function _resolveTargetPart() {
  const sel = shared.selectedNode;
  if (sel) return sel;
  const checked = tabs[activeTab].tree?.getCheckedNodeId();
  if (checked) return checked;
  return null;
}

function _showTargetHint() {
  _showModal('请选择目标部件',
    '在左侧 <b>结构树</b> 中:<br>' +
    '<ul style="margin:8px 0;padding-left:20px;line-height:1.7">' +
    '<li><b>点击</b> 单个零件 → 分析该零件的拆卸依赖链</li>' +
    '<li><b>勾选</b> 子装配节点 → 分析该子装配的整体拆卸依赖链</li>' +
    '</ul>' +
    '<p style="color:#7ec8e3;font-size:11px;margin-top:8px">' +
    '支持任何层级节点：leaf 零件 / 子装配 / BOM 单元</p>');
  const container = document.getElementById('tree-container');
  if (container) {
    container.style.transition = 'box-shadow 0.3s';
    container.style.boxShadow = '0 0 12px 2px #7ec8e3';
    setTimeout(() => { container.style.boxShadow = ''; }, 1800);
  }
}

function _showModal(title, htmlBody) {
  const existing = document.getElementById('app-modal-overlay');
  if (existing) existing.remove();
  const overlay = document.createElement('div');
  overlay.id = 'app-modal-overlay';
  overlay.style.cssText =
    'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.55);' +
    'z-index:9999;display:flex;align-items:center;justify-content:center;';
  const box = document.createElement('div');
  box.style.cssText =
    'background:#1a1a2e;color:#e0e0e0;border:1px solid #3a3a5a;border-radius:6px;' +
    'padding:20px 24px;max-width:480px;min-width:320px;font-size:13px;' +
    'box-shadow:0 6px 24px rgba(0,0,0,0.6);';
  box.innerHTML =
    '<h3 style="margin:0 0 12px 0;font-size:15px;color:#7ec8e3">' + title + '</h3>' +
    '<div style="line-height:1.6">' + htmlBody + '</div>' +
    '<div style="text-align:right;margin-top:16px">' +
    '<button id="app-modal-ok" style="padding:6px 16px;background:#2a5a8c;color:#fff;' +
    'border:none;border-radius:3px;cursor:pointer;font-size:12px">知道了</button>' +
    '</div>';
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  document.getElementById('app-modal-ok').onclick = close;
  overlay.onclick = (e) => { if (e.target === overlay) close(); };
}

function _renderChainResult(payload) {
  _activeChainPayload = payload;
  const log = document.getElementById('pipeline-log-placeholder');
  if (!log) return;
  const target = payload.target || '?';
  const resolved = payload.resolved_target && payload.resolved_target !== target
    ? ' → ' + payload.resolved_target : '';
  const chain = payload.chain || [];
  const feasible = payload.feasible_count ?? 0;
  const blocked = payload.blocked_count ?? 0;
  const total = payload.total_count ?? chain.length;
  const optEnabled = payload.optimize_direction !== false;

  let html = '<div style="color:#7ec8e3;font-weight:bold;margin-bottom:4px">';
  html += '依赖链分析: ' + target + resolved + '</div>';
  html += '<div style="margin-bottom:6px;color:#aaa">';
  html += '共 ' + total + ' 件, 可行 ' + feasible;
  html += (blocked > 0 ? ', <span style="color:#ff8a4a">阻塞 ' + blocked + '</span>' : '');
  if (optEnabled) {
    html += ' <span style="color:#7ec8e3">[方向最优化]</span>';
  }
  html += '</div>';

  const considered = payload.considered_directions || [];
  if (optEnabled && considered.length > 1) {
    html += '<div style="margin:6px 0 4px 0;color:#7ec8e3;font-weight:bold">方向比较</div>';
    html += '<div style="background:#0a1a2a;padding:4px 6px;margin-bottom:6px;border-radius:3px">';
    html += '<table style="width:100%;font-size:10px;border-collapse:collapse">';
    html += '<tr style="color:#7ec8e3;border-bottom:1px solid #3a3a5a">' +
            '<th style="text-align:left;padding:2px">方向</th>' +
            '<th style="text-align:right;padding:2px">直接阻挡</th>' +
            '<th style="text-align:right;padding:2px">递归总件数</th>' +
            '<th style="text-align:center;padding:2px">选用</th></tr>';
    for (const c of considered) {
      const dirStr = (c.direction || []).map(v => v.toFixed(1)).join(',');
      const sel = c.selected
        ? '<span style="color:#ffd24a;font-weight:bold">★</span>'
        : (c.pruned ? '<span style="color:#888">剪枝</span>' : '');
      const costStr = c.chain_cost === null ? '—' : (c.chain_cost ?? '?');
      const rowStyle = c.selected
        ? 'background:#1a2a4a;color:#ffd24a'
        : 'color:#9acdff';
      html += '<tr style="' + rowStyle + '">' +
              '<td style="padding:1px 2px">[' + dirStr + ']</td>' +
              '<td style="text-align:right;padding:1px 2px">' + (c.blockers_count ?? '?') + '</td>' +
              '<td style="text-align:right;padding:1px 2px">' + costStr + '</td>' +
              '<td style="text-align:center;padding:1px 2px">' + sel + '</td></tr>';
    }
    html += '</table></div>';
  }

  html += '<div style="margin:6px 0 4px 0;color:#7ec8e3;font-weight:bold">拆卸链路</div>';
  for (let i = 0; i < chain.length; i++) {
    const stg = chain[i];
    const isTarget = (i === chain.length - 1);
    const tag = isTarget
      ? '<span style="color:#ffd24a">★ 目标</span>'
      : '<span style="color:#9acdff">阻挡件</span>';
    const partsStr = (stg.parts || []).join(', ');
    html += '<div style="padding:2px 0;border-left:2px solid ' +
      (isTarget ? '#ffd24a' : '#3a3a5a') +
      ';padding-left:6px;margin:2px 0">' +
      'Stage ' + stg.stage + ' [' + tag + ']: ' + partsStr + '</div>';
  }
  log.innerHTML = html;
  statusBar.textContent = '依赖链分析完成: ' + total + ' 件 (' + feasible + ' 可行)';
}

function buildActiveTree() {
  const container = document.getElementById('tree-container');
  if (!container || !shared.assembly) return;
  const t = tabs[activeTab];
  t.tree = new TreeView(container, {
    onSelect: (nodeId, partIds) => {
      shared.selectedNode = nodeId;
      _highlightParts(partIds);
      if (partIds.length > 0) {
        const box = new THREE.Box3();
        const idSet = new Set(partIds);
        for (const mesh of shared.meshes) {
          if (idSet.has(mesh.userData.partId)) {
            box.expandByObject(mesh);
          }
        }
        if (!box.isEmpty()) {
          const c = new THREE.Vector3(); box.getCenter(c);
          const sz = new THREE.Vector3(); box.getSize(sz);
          const diag = Math.sqrt(sz.x * sz.x + sz.y * sz.y + sz.z * sz.z);
          sm.focusOn(c, diag);
        }
      }
      statusBar.textContent = '选中: ' + nodeId + ' (' + partIds.length + ' 零件)';
    },
    onColorChange: (partIds, color) => {
      const c = new THREE.Color(color);
      const idSet = new Set(partIds);
      for (const mesh of shared.meshes) {
        if (idSet.has(mesh.userData.partId) && mesh.material) {
          const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
          for (const mat of mats) { if (mat.color) mat.color.copy(c); }
        }
      }
    },
    onCheckChange: (nodeId, partIds) => {
      shared.checkedPartIds = new Set(partIds);
      if (t.tree && typeof t.tree.getCheckedNodes === 'function') {
        shared.checkedNodes = t.tree.getCheckedNodes();
      }
      _rebuildExplosionGroups();
      if (nodeId) {
        _highlightParts([...partIds]);
        statusBar.textContent = '编组: ' + nodeId + ' (' + partIds.size + ' 零件)';
      } else {
        _clearHighlight();
        statusBar.textContent = '已取消编组';
      }
    },
    onVisibilityChange: (nodeId, partIds, visible) => {
      if (visible) {
        for (const pid of partIds) shared.hiddenPartIds.delete(pid);
      } else {
        for (const pid of partIds) shared.hiddenPartIds.add(pid);
      }
      _applyVisibilityToScene();
      const count = shared.hiddenPartIds.size;
      statusBar.textContent = visible ? '已显示: ' + partIds.length + ' 零件' : '已隐藏: ' + partIds.length + ' 零件 (共' + count + ')';
    },
    onCompoundSelect: (name, members) => {
      shared.selectedNode = name;
      _highlightParts(members);
      statusBar.textContent = '选中编组: ' + name + ' (' + members.length + ' 件)';
      const d1 = document.getElementById('sel-node-display');
      if (d1) d1.textContent = name;
      const d2 = document.getElementById('sel-node-display-manual');
      if (d2) d2.textContent = name;
    },
    onCompoundFocus: (name, members) => {
      if (members.length > 0) {
        const box = new THREE.Box3();
        const idSet = new Set(members);
        for (const mesh of shared.meshes) {
          if (idSet.has(mesh.userData.partId)) {
            box.expandByObject(mesh);
          }
        }
        if (!box.isEmpty()) {
          const c = new THREE.Vector3(); box.getCenter(c);
          const sz = new THREE.Vector3(); box.getSize(sz);
          const diag = Math.sqrt(sz.x * sz.x + sz.y * sz.y + sz.z * sz.z);
          sm.focusOn(c, diag);
        }
      }
    },
    onCompoundColorChange: (name, row) => {
      const c = _randColor();
      if (row) row.querySelector('.swatch').style.background = c;
      const comp = shared.compounds.find(x => x.name === name);
      if (!comp) return;
      for (const pid of comp.members) {
        const colorObj = new THREE.Color(c);
        for (const mesh of shared.meshes) {
          if (mesh.userData.partId === pid && mesh.material) {
            const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
            for (const mat of mats) { if (mat.color) mat.color.copy(colorObj); }
          }
        }
      }
    },
  });
  t.tree.build(shared.hierarchy, shared.assembly.parts, shared.assembly.stages);
  t.tree.setFixedPartIds(shared.fixedPartIds);
  t.tree.setHiddenPartIds(shared.hiddenPartIds);
  _restoreCompoundsToTree();
  _updateCompoundPreview();
}

function _rebuildExplosionGroups() {
  if (!shared.assembly || !shared.loaded) return;
  const baseGroups = AssemblyLoader._buildGroups(shared.assembly, shared.loaded);
  if (shared.checkedPartIds.size === 0) {
    shared.groups = baseGroups;
    sharedExplo.loadAssemblyGroups(shared.groups);
    return;
  }
  const mergedMeshes = [];
  const otherGroups = [];
  for (const g of baseGroups) {
    const allIn = g.meshes.every(m => shared.checkedPartIds.has(m.userData.partId));
    if (allIn) {
      mergedMeshes.push(...g.meshes);
    } else {
      otherGroups.push(g);
    }
  }
  if (mergedMeshes.length > 0) {
    otherGroups.unshift({
      id: '__merged__',
      name: '[编组]',
      meshes: mergedMeshes,
      direction: '+Y',
      distanceMultiplier: 1.0,
      stage: 1,
    });
  }
  shared.groups = otherGroups;
  sharedExplo.loadAssemblyGroups(shared.groups);
}

// ── Load Assembly ────────────────────────────────────────

function _glbPath(dir, glbFile) {
  const baseName = glbFile.replace(/^.*[\\/]/, '');
  return dir.replace(/\\/g, '/').replace(/\/$/, '') + '/parts/' + baseName;
}

// ── Helpers: dispose Three.js resources ─────────────────

function _disposeObject3D(obj) {
  obj.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    const mats = Array.isArray(o.material) ? o.material : (o.material ? [o.material] : []);
    for (const m of mats) {
      for (const k of ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap', 'aoMap', 'specularMap']) {
        if (m[k] && typeof m[k].dispose === 'function') m[k].dispose();
      }
      if (typeof m.dispose === 'function') m.dispose();
    }
  });
}

async function _loadModelCore(assembly, dir) {
  // Clear previous models from scene AND dispose their GPU resources
  if (shared.loaded) {
    for (const [, p] of shared.loaded) {
      if (p.modelData && p.modelData.scene) {
        sm.scene.remove(p.modelData.scene);
        _disposeObject3D(p.modelData.scene);
      }
    }
  }

  // Reset all shared state to avoid stale references
  shared.hiddenPartIds = new Set();
  shared.checkedPartIds = new Set();
  shared.checkedNodes = [];
  shared.fixedPartIds = new Set();
  shared.selectedNode = null;
  shared.bomEntries = [];
  _highlightedParts.length = 0;
  if (sharedExplo && typeof sharedExplo._clearGroups === 'function') sharedExplo._clearGroups();
  if (annot && typeof annot.clear === 'function') annot.clear();

  shared.assembly = assembly;
  shared.loaded = new Map();
  shared.meshes = [];

  if (assembly.chainInfo) {
    _renderChainResult(assembly.chainInfo);
  }

  let meshCount = 0;
  let skipCount = 0;
  for (const part of assembly.parts) {
    const glbPath = _glbPath(dir, part.glbFile);
    if (!(await window.electronAPI.fileExists(glbPath))) {
      skipCount++;
      if (skipCount <= 3) _logPipeline('SKIP: ' + glbPath + ' (file not found or access denied)');
      continue;
    }
    // Encode each path segment to be URL-safe for the local:// protocol
    const segments = glbPath.replace(/\\/g, '/').split('/');
    const drive = (segments[0].indexOf(':') >= 0) ? segments.shift().replace(':', '') : '';
    const pathPart = segments.map(seg => encodeURIComponent(seg)).join('/');
    const url = drive ? ('local://' + drive.toLowerCase() + '/' + pathPart) : ('local:///' + pathPart);
    try {
      const data = await modelLoader.loadModel(url);
      shared.loaded.set(part.id, { ...part, modelData: data, meshes: data.meshes });
    } catch (e) {
      skipCount++;
      _logPipeline('FAIL: ' + url + ' — ' + (e && e.message || e));
    }
  }
  if (skipCount > 0) {
    _logPipeline('WARNING: ' + skipCount + '/' + assembly.parts.length + ' parts skipped (paths above)');
  }

  for (const [, p] of shared.loaded) {
    for (const m of p.meshes) {
      m.userData.partId = p.id;
      if (p.isExplosionCenter) m.userData.isExplosionCenter = true;
      shared.meshes.push(m);
      meshCount++;
      if (!m.material) {
        m.material = new THREE.MeshStandardMaterial({ color: p.color || 0x0080c0, roughness: 0.5, metalness: 0.0, side: THREE.DoubleSide });
      } else if (Array.isArray(m.material)) {
        for (const mat of m.material) {
          mat.side = THREE.DoubleSide;
          if (mat.color && mat.color.getHex() === 0xffffff && !p.color) mat.color.set(0x0080c0);
        }
      } else {
        m.material.side = THREE.DoubleSide;
        if (m.material.color && m.material.color.getHex() === 0xffffff && !p.color) {
          m.material.color.set(0x0080c0);
        }
      }
    }
    if (p.modelData && p.modelData.scene) {
      if (p.transform && Array.isArray(p.transform) && p.transform.length === 16) {
        const mat = new THREE.Matrix4();
        mat.fromArray(p.transform);
        p.modelData.scene.applyMatrix4(mat);
      }
      sm.scene.add(p.modelData.scene);
    }
  }

  shared.groups = AssemblyLoader._buildGroups(assembly, shared.loaded);
  shared.hierarchy = assembly.hierarchy || [];
  sharedExplo.loadAssemblyGroups(shared.groups);

  _logPipeline('Loaded ' + shared.loaded.size + ' parts, ' + meshCount + ' meshes');
  _logPipeline('Scene children: ' + sm.scene.children.length);

  _applyVisibilityToScene();
  buildActiveTree();
  _focusCamera();
  _setupViewportClick();
}

function _focusCamera() {
  sm.scene.updateMatrixWorld();
  const bbox = sm.getSceneBBox();
  if (!bbox.isEmpty()) {
    const center = new THREE.Vector3(); bbox.getCenter(center);
    const size = new THREE.Vector3(); bbox.getSize(size);
    const diagonal = Math.sqrt(size.x * size.x + size.y * size.y + size.z * size.z);
    sm.focusOn(center, diagonal);
  } else {
    sm.resetCamera();
  }
  sm.controls.update();
  sm.renderer.render(sm.scene, sm.camera);
}

async function _loadPipelineResult(jsonPath, targetIdx) {
  const dir = jsonPath.replace(/[\\/][^\\/]*$/, '');
  const buf = await window.electronAPI.readFile(jsonPath);
  const content = new TextDecoder().decode(buf);
  const assembly = JSON.parse(content);
  await _loadModelCore(assembly, dir);

  const n = assembly.parts.length;
  statusBar.textContent = '管线完成 — ' + n + ' 零件';

  if (targetIdx !== activeTab) switchTab(targetIdx);
}

async function loadAssembly() {
  if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
  const result = await window.electronAPI.selectAssemblyJson();
  if (!result) return;
  statusBar.textContent = '加载中...';
  try {
    const assembly = JSON.parse(result.content);
    await _loadModelCore(assembly, result.dir);

    const n = assembly.parts.length;
    statusBar.textContent = '已加载 ' + n + ' 零件, ' + shared.loaded.size + ' loaded';

    sm.renderer.render(sm.scene, sm.camera);
  } catch (err) {
    statusBar.textContent = '加载失败: ' + err.message;
    console.error(err);
  }
}

// ── Viewport Click → Highlight Part ───────────────────────

let _highlightedParts = [];

function _applyVisibilityToScene() {
  for (const mesh of shared.meshes) {
    mesh.visible = !shared.hiddenPartIds.has(mesh.userData.partId);
  }
}

function _setPartVisibility(partId, visible) {
  for (const mesh of shared.meshes) {
    if (mesh.userData.partId === partId) {
      mesh.visible = visible;
    }
  }
}

function _clearHighlight() {
  for (const entry of _highlightedParts) {
    if (entry.mesh.material) {
      const mats = Array.isArray(entry.mesh.material) ? entry.mesh.material : [entry.mesh.material];
      for (const mat of mats) { if (mat.emissive) mat.emissive.copy(entry.originalEmissive); }
    }
  }
  _highlightedParts = [];
}

function _highlightPart(partId) {
  _highlightParts([partId]);
}

function _highlightParts(partIds) {
  _clearHighlight();
  const idSet = new Set(partIds);
  const targetMeshes = shared.meshes.filter(m => idSet.has(m.userData.partId));
  for (const mesh of targetMeshes) {
    if (!mesh.material) continue;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const mat of mats) {
      if (mat.emissive) {
        _highlightedParts.push({ mesh, originalEmissive: mat.emissive.clone() });
        mat.emissive.set(0x3388ff);
        mat.emissiveIntensity = 0.6;
      }
    }
  }
}

let _viewportPointerHandler = null;
const _CLICK_THRESHOLD = 5;

function _setupViewportClick() {
  const canvas = sm.renderer.domElement;
  canvas.style.cursor = 'default';
  if (_viewportPointerHandler) {
    canvas.removeEventListener('pointerdown', _viewportPointerHandler.down);
    canvas.removeEventListener('pointerup', _viewportPointerHandler.up);
    if (_viewportPointerHandler.dblclick) {
      canvas.removeEventListener('dblclick', _viewportPointerHandler.dblclick);
    }
  }

  let pointerDownPos = null;

  const onDown = (event) => {
    pointerDownPos = { x: event.clientX, y: event.clientY };
  };

  const onUp = (event) => {
    if (!pointerDownPos) return;
    const dx = event.clientX - pointerDownPos.x;
    const dy = event.clientY - pointerDownPos.y;
    pointerDownPos = null;
    if (dx * dx + dy * dy > _CLICK_THRESHOLD * _CLICK_THRESHOLD) return;

    const rect = canvas.getBoundingClientRect();
    const mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(mouse, sm.camera);
    const intersects = raycaster.intersectObjects(shared.meshes, false);
    if (intersects.length === 0) { _clearHighlight(); statusBar.textContent = '就绪'; return; }

    const partId = intersects[0].object.userData.partId;
    if (!partId) return;

    if (event.ctrlKey || event.metaKey) {
      const tree = tabs[activeTab].tree;
      if (!tree) return;
      if (shared.checkedPartIds.has(partId)) {
        shared.checkedPartIds.delete(partId);
      } else {
        shared.checkedPartIds.add(partId);
      }
      _highlightParts([...shared.checkedPartIds]);
      statusBar.textContent = '已选编组: ' + shared.checkedPartIds.size + ' 零件';
      return;
    }

    _highlightPart(partId);
    statusBar.textContent = '选中: ' + partId;
  };

  const onDblClick = (event) => {
    const rect = canvas.getBoundingClientRect();
    const mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(mouse, sm.camera);
    const intersects = raycaster.intersectObjects(shared.meshes, false);
    if (intersects.length === 0) return;

    const partId = intersects[0].object.userData.partId;
    if (!partId) return;

    const t = tabs[activeTab];
    if (t.tree && typeof t.tree.selectNodeByPartId === 'function') {
      const found = t.tree.selectNodeByPartId(partId);
      if (found) {
        statusBar.textContent = '定位到结构树: ' + partId;
      }
    }
  };

  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('dblclick', onDblClick);
  _viewportPointerHandler = { down: onDown, up: onUp, dblclick: onDblClick };
}

// ── Export Helpers ───────────────────────────────────────

async function _exportAnnotated() {
  annot.draw();
  const comp = annot.composeToCanvas(sm.renderer.domElement);
  const dataUrl = comp.toDataURL('image/png');
  if (window.electronAPI) await window.electronAPI.saveScreenshot(dataUrl);
  else ExportManager.prototype._download(dataUrl, 'screenshot.png');
}

function _exportSimple() {
  const dataUrl = sm.renderer.domElement.toDataURL('image/png');
  if (window.electronAPI) window.electronAPI.saveScreenshot(dataUrl);
  else ExportManager.prototype._download(dataUrl, 'screenshot.png');
}

async function _exportSVG() {
  annot.draw();
  annot.updatePositions();
  const w = viewport.clientWidth;
  const h = viewport.clientHeight;
  const dataUrl = sm.renderer.domElement.toDataURL('image/png');
  const screenData = annot.getScreenData();
  let svg = exportMgr.exportSVG(dataUrl, screenData, w, h, 'export.svg');
  if (window.electronAPI) {
    await window.electronAPI.saveSVG(svg);
  } else {
    exportMgr._downloadText(svg, 'export.svg');
  }
  statusBar.textContent = 'SVG 已导出';
}

// ── Batch Position Capture ──────────────────────────────

async function _batchPositionCapture() {
  if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }

  statusBar.textContent = '正在准备批量生成位置图...';

  const setup = await window.electronAPI.selectBatchPositionFiles();
  if (!setup) { statusBar.textContent = '已取消'; return; }

  const outputDir = setup.outputDir;
  const excelFiles = setup.excelFiles;

  statusBar.textContent = '导入车壳...';
  try {
    const result = await window.electronAPI.importBodyFromPath(setup.bodyStpPath);
    if (!result || !result.ok) {
      statusBar.textContent = '车壳导入失败，请重试';
      return;
    }
    await bodyLoader.reloadBodies();
    const idx = bodyLoader.bodies.findIndex(b => b.name === result.name);
    if (idx >= 0) {
      await bodyLoader.switchBody(idx, sm.scene);
    } else {
      statusBar.textContent = '车壳加载失败: 未找到 ' + result.name;
      return;
    }
  } catch (e) {
    statusBar.textContent = '车壳导入出错: ' + (e && e.message || e);
    return;
  }

  const bodyGroup = bodyLoader.currentBody ? bodyLoader.currentBody.group : null;
  if (!bodyGroup) {
    statusBar.textContent = '车壳未加载，请先导入车壳';
    return;
  }
  _setBodyGroupOpacity(bodyGroup, 0.3);

  let totalComponents = 0;
  let successCount = 0;
  let skipCount = 0;

  for (let fi = 0; fi < excelFiles.length; fi++) {
    const excel = excelFiles[fi];
    const tableName = path__basename(excel.path, '.xlsx');

    statusBar.textContent = '[' + (fi + 1) + '/' + excelFiles.length + '] ' + tableName + ' — 管线运行中...';
    _logPipeline('=== 批量位置图: ' + tableName + ' ===');

    const jsonPath = await window.electronAPI.runBomPreviewPipelineCached(excel.path, excel.dir);
    if (!jsonPath) {
      statusBar.textContent = '[' + (fi + 1) + '/' + excelFiles.length + '] ' + tableName + ' — 管线失败，跳过';
      skipCount++;
      continue;
    }

    const dir = jsonPath.replace(/[\\/][^\\/]*$/, '');
    let buf;
    try { buf = await window.electronAPI.readFile(jsonPath); } catch (e) { skipCount++; continue; }
    const assembly = JSON.parse(new TextDecoder().decode(buf));

    statusBar.textContent = '[' + (fi + 1) + '/' + excelFiles.length + '] ' + tableName + ' — 加载部件...';
    await _loadModelCoreSilently(assembly, dir);

    const bomEntries = _buildBomEntriesFromAssembly(assembly);
    if (bomEntries.length === 0) {
      _disposeAllModels();
      skipCount++;
      continue;
    }

    for (let ci = 0; ci < bomEntries.length; ci++) {
      const entry = bomEntries[ci];

      statusBar.textContent =
        '位置图 [' + (fi + 1) + '/' + excelFiles.length + '] '
        + tableName + ' — ' + entry.name + ' (' + (ci + 1) + '/' + bomEntries.length + ')';

      try {
        await _captureSingleComponent(outputDir, tableName, entry, ci, bodyGroup, excel.dir);
        successCount++;
      } catch (e) {
        _logPipeline('捕获失败: ' + entry.name + ' — ' + (e && e.message || e));
        skipCount++;
      }
      totalComponents++;
    }

    _disposeAllModels();
  }

  statusBar.textContent =
    '批量位置图完成: ' + successCount + '/' + totalComponents + ' 组件, '
    + skipCount + ' 跳过. 输出: ' + outputDir;
  _logPipeline('=== 批量位置图完成 ===');
  _logPipeline('成功: ' + successCount + ', 跳过: ' + skipCount + ', 输出: ' + outputDir);
}

async function _captureSingleComponent(outputDir, tableName, entry, index, bodyGroup, modelsDir) {
  const partIdSet = new Set(entry.partIds);

  for (const mesh of shared.meshes) {
    mesh.visible = partIdSet.has(mesh.userData.partId);
    if (mesh.visible && mesh.material) {
      const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      for (const mat of mats) {
        if (mat.color) mat.color.setRGB(0, 0.5, 0.75);
        mat.needsUpdate = true;
      }
    }
  }

  _setBodyGroupOpacity(bodyGroup, 0.3);

  sm.viewPositionCapture();
  await new Promise(r => requestAnimationFrame(r));
  await new Promise(r => requestAnimationFrame(r));

  annot.setSingleLabel(entry.partIds, entry.name);
  annot.show();
  await new Promise(r => requestAnimationFrame(r));

  const dataUrl = _captureAnnotatedPNGDataUrl();
  const safeName = _sanitizeFilename(entry.name);
  const baseFile = outputDir.replace(/\\/g, '/').replace(/\/$/, '') + '/' + tableName + '_' + safeName;
  await window.electronAPI.saveBatchPng(dataUrl, baseFile + '.png');

  annot.updatePositions();
  const screenData = annot.getScreenData();
  let svg;
  try { svg = exportMgr.exportSVG(dataUrl, screenData || [], viewport.clientWidth, viewport.clientHeight, safeName + '.svg'); }
  catch (e) { svg = _basicSvg(dataUrl, viewport.clientWidth, viewport.clientHeight); }
  await window.electronAPI.saveBatchSvg(svg, baseFile + '.svg');

  annot.clear();
}

function _captureAnnotatedPNGDataUrl() {
  annot.draw();
  const comp = annot.composeToCanvas(sm.renderer.domElement);
  return comp.toDataURL('image/png');
}

function _basicSvg(dataUrl, w, h) {
  return '<?xml version="1.0" encoding="utf-8"?>\n'
    + '<svg version="1.1" xmlns="http://www.w3.org/2000/svg" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">\n'
    + '  <image href="' + dataUrl + '" x="0" y="0" width="' + w + '" height="' + h + '" />\n'
    + '</svg>\n';
}

function _sanitizeFilename(name) {
  return String(name || 'component').replace(/[\\/:*?"<>|]/g, '_').replace(/\s+/g, '_').substring(0, 80);
}

function path__basename(filePath, ext) {
  let base = filePath.replace(/\\/g, '/').split('/').pop();
  if (ext && base.toLowerCase().endsWith(ext.toLowerCase())) {
    base = base.substring(0, base.length - ext.length);
  }
  return base;
}

function _setBodyGroupOpacity(group, opacity) {
  if (!group) return;
  group.traverse((child) => {
    if (child.isMesh && child.material) {
      const mats = Array.isArray(child.material) ? child.material : [child.material];
      for (const mat of mats) {
        mat.transparent = opacity < 1;
        mat.opacity = opacity;
        mat.depthWrite = opacity >= 1;
        mat.needsUpdate = true;
      }
    }
  });
}

function _buildBomEntriesFromAssembly(assembly) {
  const bomMap = new Map();
  if (!assembly || !assembly.parts) return [];
  for (const part of assembly.parts) {
    const src = part.bomSource;
    if (!src || !src.code) continue;
    const key = src.code;
    if (!bomMap.has(key)) {
      bomMap.set(key, { name: src.name || key, code: key, partIds: [] });
    }
    bomMap.get(key).partIds.push(part.id);
  }
  return Array.from(bomMap.values());
}

async function _loadModelCoreSilently(assembly, dir) {
  _disposeAllModels();

  shared.assembly = assembly;
  shared.loaded = new Map();
  shared.meshes = [];
  shared.hiddenPartIds = new Set();
  shared.checkedPartIds = new Set();
  shared.checkedNodes = [];
  shared.fixedPartIds = new Set();
  shared.selectedNode = null;
  shared.bomEntries = [];
  _highlightedParts.length = 0;

  let meshCount = 0;
  for (const part of assembly.parts) {
    const glbPath = _glbPath(dir, part.glbFile);
    let exists = false;
    try { exists = (await window.electronAPI.fileExists(glbPath)) || false; } catch (e) {}
    if (!exists) continue;
    const segments = glbPath.replace(/\\/g, '/').split('/');
    const drive = (segments[0].indexOf(':') >= 0) ? segments.shift().replace(':', '') : '';
    const pathPart = segments.map(seg => encodeURIComponent(seg)).join('/');
    const url = drive ? ('local://' + drive.toLowerCase() + '/' + pathPart) : ('local:///' + pathPart);
    try {
      const data = await modelLoader.loadModel(url);
      shared.loaded.set(part.id, { ...part, modelData: data, meshes: data.meshes });
    } catch (e) {}
  }

  for (const [, p] of shared.loaded) {
    for (const m of p.meshes) {
      m.userData.partId = p.id;
      if (p.isExplosionCenter) m.userData.isExplosionCenter = true;
      shared.meshes.push(m);
      meshCount++;
      if (!m.material) {
        m.material = new THREE.MeshStandardMaterial({ color: p.color || 0x0080c0, roughness: 0.5, metalness: 0.0, side: THREE.DoubleSide });
      } else if (Array.isArray(m.material)) {
        for (const mat of m.material) {
          mat.side = THREE.DoubleSide;
          if (mat.color && mat.color.getHex() === 0xffffff && !p.color) mat.color.set(0x0080c0);
        }
      } else {
        m.material.side = THREE.DoubleSide;
        if (m.material.color && m.material.color.getHex() === 0xffffff && !p.color) {
          m.material.color.set(0x0080c0);
        }
      }
    }
    if (p.modelData && p.modelData.scene) {
      if (p.transform && Array.isArray(p.transform) && p.transform.length === 16) {
        const mat = new THREE.Matrix4();
        mat.fromArray(p.transform);
        p.modelData.scene.applyMatrix4(mat);
      }
      sm.scene.add(p.modelData.scene);
    }
  }

  shared.groups = AssemblyLoader._buildGroups(assembly, shared.loaded);
  shared.hierarchy = assembly.hierarchy || [];
}

function _disposeAllModels() {
  for (const [, p] of (shared.loaded || new Map())) {
    if (p.modelData && p.modelData.scene) {
      sm.scene.remove(p.modelData.scene);
      p.modelData.scene.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        const mats = Array.isArray(o.material) ? o.material : (o.material ? [o.material] : []);
        for (const m of mats) {
          for (const k of ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap', 'aoMap', 'specularMap']) {
            if (m[k] && typeof m[k].dispose === 'function') m[k].dispose();
          }
          if (typeof m.dispose === 'function') m.dispose();
        }
      });
    }
  }
  shared.loaded = new Map();
  shared.meshes = [];
  shared.assembly = null;
}

// ── Pipeline Progress ───────────────────────────────────

function _logPipeline(msg) {
  if (!msg) return;
  const log = getPipelineLog();
  if (!log) return;
  log.classList.add('visible');
  const line = document.createElement('div');
  line.textContent = msg;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

if (window.electronAPI) {
  window.electronAPI.onPipelineProgress((msg) => {
    if (typeof msg === 'string') {
      const idx = msg.indexOf('CHAIN_RESULT_JSON:');
      if (idx >= 0) {
        try {
          const jsonStr = msg.slice(idx + 'CHAIN_RESULT_JSON:'.length).trim();
          const payload = JSON.parse(jsonStr);
          _renderChainResult(payload);
        } catch (e) {
          console.warn('Failed to parse CHAIN_RESULT_JSON:', e);
        }
        return;
      }
      const cmpIdx = msg.indexOf('COMPARE_RESULT_JSON:');
      if (cmpIdx >= 0) {
        try {
          const jsonStr = msg.slice(cmpIdx + 'COMPARE_RESULT_JSON:'.length).trim();
          const payload = JSON.parse(jsonStr);
          _renderCompareResult(payload);
          statusBar.textContent = '对比完成: ' + (payload.total_pairs || 0) + ' 对';
        } catch (e) {
          console.warn('Failed to parse COMPARE_RESULT_JSON:', e);
        }
        return;
      }
    }
    _logPipeline(msg);
  });
  window.electronAPI.onPipelineMode((mode) => { pipelineMode = mode; });
  window.electronAPI.onPipelineStarted((stpPath) => {
    if (stpPath) {
      shared.sourceStpPath = stpPath;
      if (pipelineMode === 'bom-preview' || pipelineMode === 'bom-full') {
        shared.bomSourcePath = stpPath;
        shared.bomModelsDir = null;
      }
    }
    const log = getPipelineLog();
    if (log) log.innerHTML = '';
    _logPipeline('管线启动...');
    if (pipelineMode === 'full' || pipelineMode === 'bom-full') switchTab(4);
    if (pipelineMode === 'clean') switchTab(1);
    if (pipelineMode === 'compare') switchTab(0);
  });
  let _loadingChain = Promise.resolve();
  window.electronAPI.onPipelineComplete((jsonPath) => {
    _loadingChain = _loadingChain.then(async () => {
      try {
        _logPipeline('完成! ' + jsonPath);
        let targetIdx = activeTab;
        if (pipelineMode === 'full' || pipelineMode === 'bom-full') targetIdx = 4;
        if (pipelineMode === 'clean') targetIdx = 1;
        if (pipelineMode === 'compare') {
          targetIdx = 0;
          statusBar.textContent = '数模对比完成';
          return;
        }
        await _loadPipelineResult(jsonPath, targetIdx);
        _renderBomList();
        _restoreCompoundsToTree();
      } catch (err) {
        console.error('pipeline complete load failed:', err);
        statusBar.textContent = '加载失败: ' + (err && err.message);
      }
    });
  });
}

// ── Electron Menu Events ─────────────────────────────────
if (window.electronAPI) {
  window.electronAPI.onMenuLoadAssembly(async () => { await loadAssembly(); });
  window.electronAPI.onMenuResetCamera(() => sm.resetCamera());
  window.electronAPI.onMenuScreenshot(() => _exportAnnotated());
  window.electronAPI.onMenuViewLeftRear(() => sm.viewLeftRear());
  window.electronAPI.onMenuViewLeftFront(() => sm.viewLeftFront());
  window.electronAPI.onMenuViewRightRear(() => sm.viewRightRear());
  window.electronAPI.onMenuViewRightFront(() => sm.viewRightFront());
  window.electronAPI.onMenuViewTop(() => sm.viewTop());
  window.electronAPI.onMenuViewBottom(() => sm.viewBottom());
  window.electronAPI.onMenuViewPositionCapture(() => sm.viewPositionCapture());
  window.electronAPI.onMenuShowHelp(() => _showHelpCarousel(0));
}

// ── Startup ──────────────────────────────────────────────
bodyLoader.loadManifest('bodies/manifest.json', modelLoader)
  .then(() => renderPanel(0))
  .catch((err) => {
    console.error('body manifest load failed:', err);
    renderPanel(0);
  });

// Global error handlers
window.addEventListener('unhandledrejection', (e) => {
  console.error('unhandled rejection:', e.reason);
  if (statusBar) statusBar.textContent = '错误: ' + (e.reason && e.reason.message || e.reason);
});
window.addEventListener('error', (e) => {
  console.error('runtime error:', e.message);
});

sm.renderer.domElement.style.display = 'block';
statusBar.textContent = '就绪 — Ctrl+O 加载STEP | Ctrl+B 加载BOM | 视图菜单切换视角';
