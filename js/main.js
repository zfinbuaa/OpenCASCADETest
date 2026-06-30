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
  { mode: 'position', tree: null },
  { mode: 'explosion', tree: null },
  { mode: 'serviceability', tree: null },
  { mode: 'manual', tree: null },
  { mode: 'clean', tree: null },
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

  const titles = ['位置图', '爆炸图', '拆装方案（可维修性）', '拆装方案（维修手册）', '数模清洗', '帮助'];
  panelHeader.textContent = titles[idx];

  renderPanel(idx);
  statusBar.textContent = '就绪';
}

// ── Render Panel ─────────────────────────────────────────
function renderPanel(idx) {
  panelBody.innerHTML = '';
  switch (idx) {
    case 0: renderPositionPanel(); break;
    case 1: renderExplosionPanel(); break;
    case 2: renderServiceabilityPanel(); break;
    case 3: renderManualPanel(); break;
    case 4: renderCleanPanel(); break;
    case 5: renderHelpPanel(); break;
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

function renderPositionPanel() {
  let h = '';
  h += '<div class="section-title">数据加载</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-load-bom">加载 BOM (多文件)</button>';
  h += '<button class="btn btn-outline" id="btn-load-assembly">加载 JSON</button>';
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
  h += '<div class="section-title">数据加载</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-load-bom-explosion">加载 BOM + 分析</button>';
  h += '<button class="btn btn-outline" id="btn-load">加载 JSON</button>';
  h += '</div>';
  h += '<div class="section-title">爆炸中心</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-set-center">选中爆炸中心</button>';
  h += '<button class="btn btn-outline" id="btn-clear-center">清除中心</button>';
  h += '</div>';
  h += '<div id="center-display" style="margin:4px 12px;font-size:11px;color:#7ec8e3;">未设置 (使用几何重心)</div>';
  h += '<div class="section-title">标注管理</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-create-compound">勾选部件 → 生成标注</button>';
  h += '<button class="btn btn-outline" id="btn-clear-compounds">清空标注</button>';
  h += '</div>';
  h += '<div id="compound-preview" style="margin:4px 10px;font-size:10px;color:#889;min-height:18px;">未创建标注</div>';
  h += '<div class="section-title">爆炸控制</div>';
  h += '<div class="slider-row"><span>距离</span><input type="range" id="slider-dist" min="10" max="2000" value="150" step="5"><span id="val-dist">150</span>mm</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-explode">逐阶段爆炸</button>';
  h += '<button class="btn btn-outline" id="btn-explode-instant">一键爆炸</button>';
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

function renderHelpPanel() {
  panelBody.innerHTML = `
    <div class="section-title">帮助</div>
    <div class="help-content" style="padding:8px 16px;font-size:12px;line-height:1.7;overflow-y:auto;max-height:calc(100vh - 160px);">
      <h3 style="color:#7ec8e3;margin:12px 0 6px;">系统架构</h3>
      <p>基于 OpenCASCADE (OCCT) + Three.js + Electron 的三维装配体自动拆装方案生成与可视化系统。</p>
      <p>输入 STEP (.stp) 格式的三维装配体模型，自动输出拆装顺序、爆炸方向、碰撞验证报告，并在桌面前端以交互式爆炸图展示。</p>
      
      <h3 style="color:#7ec8e3;margin:12px 0 6px;">功能页面</h3>
      
      <details open><summary style="cursor:pointer;color:#7ec8e3;font-weight:600;">位置图</summary>
        <p style="padding-left:12px;">以原始装配位置查看全部零件。左侧结构树点击零件可高亮聚焦。右侧面板可切换车壳叠加显示。</p>
        <p style="padding-left:12px;">操作: 菜单 文件 → 加载单个 STEP 文件 (Ctrl+O) 或 通过表格加载多个 STEP 文件 (Ctrl+B)。</p>
      </details>

      <details open><summary style="cursor:pointer;color:#7ec8e3;font-weight:600;">爆炸图</summary>
        <p style="padding-left:12px;">查看零件的爆炸分解视图，支持手动调整爆炸程度和位置。通过右侧滑块控制爆炸程度(0%-100%)，或点击"一键爆炸"立即展开。</p>
        <p style="padding-left:12px;">支持 TransformControls 手动拖拽调整单个零件位置，可选显示推力线标注爆炸方向。</p>
      </details>

      <details open><summary style="cursor:pointer;color:#7ec8e3;font-weight:600;">拆装方案（可维修性）</summary>
        <p style="padding-left:12px;">为整个装配体生成完整的分阶段拆卸序列。菜单 管线 → 生成拆装方案 (Ctrl+G) 触发8步分析管线。</p>
        <p style="padding-left:12px;">产出: 分阶段拆卸顺序列表 + 步骤动画演示。编组管理: 勾选零件后可根据标注生成编组。</p>
      </details>

      <details open><summary style="cursor:pointer;color:#7ec8e3;font-weight:600;">拆装方案（维修手册）</summary>
        <p style="padding-left:12px;">针对指定目标零件，计算"要拆这个零件必须先拆哪些"的完整依赖链条。</p>
        <p style="padding-left:12px;">算法: 从26个候选方向中选出最优8个 → 并行碰撞检测 → 光束搜索(K=4)递归模拟总拆卸成本 → 选择最优方向。</p>
        <p style="padding-left:12px;">产出: 依赖链概要 + 方向对比表 + 逐阶段拆卸顺序 + AI最佳拆装路径动画。</p>
      </details>

      <details open><summary style="cursor:pointer;color:#7ec8e3;font-weight:600;">数模清洗</summary>
        <p style="padding-left:12px;">按BOM表格J列匹配零件 + 干涉检查 + 去重清洗模型。菜单 文件 → 数模清洗 (STP + BOM)。</p>
      </details>

      <h3 style="color:#7ec8e3;margin:12px 0 6px;">BOM多文件加载</h3>
      <p>当装配体零件分散在多个STEP文件中时，通过Excel表格统一加载。H列为零件名称，J列为零件编码。</p>

      <h3 style="color:#7ec8e3;margin:12px 0 6px;">碰撞检测</h3>
      <p>零件沿拆卸方向扫掠100mm(默认值)。若能无障碍移动100mm则视为"可以拆除"。可通过 --explosion-distance 参数调整。</p>

      <h3 style="color:#7ec8e3;margin:12px 0 6px;">安装与运行</h3>
      <p>环境: Node.js ≥ 16, Python ≥ 3.10 + conda, pythonocc-core ≥ 7.8</p>
      <p>开发运行: npm start | 构建: build_portable.bat</p>

      <h3 style="color:#7ec8e3;margin:12px 0 6px;">命令行管线</h3>
      <pre style="background:#0a0a1a;padding:8px;border-radius:3px;font-size:10px;overflow-x:auto;">
python pipeline.py input.stp --preview --output-dir ./output/
python pipeline.py input.stp --output-dir ./output/
python pipeline.py input.stp --output-dir ./output/ --skip-collision
python pipeline.py input.stp --output-dir ./output/ --explosion-distance 200
python pipeline.py assembly.json --validate --output-dir ./output/</pre>

      <h3 style="color:#7ec8e3;margin:12px 0 6px;">输出格式</h3>
      <p>assembly.json + parts/*.glb + report.txt</p>
      <p>零件字段: id, name, glbFile, isFastener, disassemblyStage, direction, distanceMultiplier, directionConfidence, color</p>

      <h3 style="color:#7ec8e3;margin:12px 0 6px;">技术栈</h3>
      <p>几何内核: OpenCASCADE 7.9 | 3D渲染: Three.js 0.157 | 桌面框架: Electron | 打包: PyInstaller + electron-builder</p>
      <p style="margin-top:16px;color:#889;">版本 2.0 | License: LGPL-3.0</p>
    </div>`;
  _bindHelpPanel();
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
    statusBar.textContent = '加载 BOM 中...';
    await window.electronAPI.runBomPreviewPipeline();
  });

  document.getElementById('btn-load-assembly')?.addEventListener('click', loadAssembly);

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

function bindExplosionPanel() {
  document.getElementById('btn-load-bom-explosion')?.addEventListener('click', async () => {
    if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
    statusBar.textContent = 'BOM 全管线分析中...';
    await window.electronAPI.runBomFullPipeline(null);
  });
  document.getElementById('btn-load')?.addEventListener('click', loadAssembly);

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
  });

  document.getElementById('btn-clear-center')?.addEventListener('click', () => {
    shared.explosionCenter = null;
    _updateCenterDisplay();
    statusBar.textContent = '已清除爆炸中心 (将使用几何重心)';
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
  document.getElementById('btn-explode')?.addEventListener('click', () => {
    const center = sharedExplo.findCenterPoint(shared.explosionCenter);
    sharedExplo.radialExplodeAnimated(center, sharedExplo.explosionDistance, 800, shared.compounds);
  });
  document.getElementById('btn-explode-instant')?.addEventListener('click', () => {
    const center = sharedExplo.findCenterPoint(shared.explosionCenter);
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
        for (const mesh of shared.meshes) {
          if (mesh.userData.partId === partIds[0]) {
            const box = new THREE.Box3().setFromObject(mesh);
            const c = new THREE.Vector3(); box.getCenter(c);
            sm.focusOn(c, 300); break;
          }
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
        const pid = members[0];
        for (const mesh of shared.meshes) {
          if (mesh.userData.partId === pid) {
            const box = new THREE.Box3().setFromObject(mesh);
            const c = new THREE.Vector3(); box.getCenter(c);
            sm.focusOn(c, 300); break;
          }
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
    if (pipelineMode === 'full' || pipelineMode === 'bom-full') switchTab(2);
    if (pipelineMode === 'clean') switchTab(4);
  });
  // Serialize concurrent pipeline-complete events so double-clicks don't race.
  let _loadingChain = Promise.resolve();
  window.electronAPI.onPipelineComplete((jsonPath) => {
    _loadingChain = _loadingChain.then(async () => {
      try {
        _logPipeline('完成! ' + jsonPath);
        let targetIdx = activeTab;
        if (pipelineMode === 'full' || pipelineMode === 'bom-full') targetIdx = 2;
        if (pipelineMode === 'clean') targetIdx = 0;
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
  window.electronAPI.onMenuShowHelp(() => switchTab(5));
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
