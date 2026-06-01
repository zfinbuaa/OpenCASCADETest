/**
 * 整车数模自动拆装方案系统 — Electron 桌面应用
 *
 * 单一场景架构：一个视口 + 一个渲染器 + 一个场景。
 * 三个 Tab 共享同一份模型数据（shared），各自维护独立的爆炸/树状态。
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
const sm = new SceneManager(viewport, { backgroundColor: 0xffffff });
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
  hiddenPartIds: new Set(),
  bomEntries: [],
  bomSourcePath: null,
  bomModelsDir: null,
};

// ── Per-tab state ─────────────────────────────────────────
const tabs = [
  { mode: 'position', tree: null },
  { mode: 'explosion', tree: null },
  { mode: 'sequence', tree: null },
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

  const titles = ['位置图', '爆炸图', '拆装方案'];
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
    case 2: renderDisassemblyPanel(); break;
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
  h += '<div class="section-title">视角</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-vfront">前视</button>';
  h += '<button class="btn btn-outline" id="btn-vback">后视</button>';
  h += '<button class="btn btn-outline" id="btn-vleft">左视</button>';
  h += '<button class="btn btn-outline" id="btn-vright">右视</button>';
  h += '<button class="btn btn-outline" id="btn-vtop">俯视</button>';
  h += '<button class="btn btn-outline" id="btn-viso">等轴测</button>';
  h += '</div>';
  h += '<div class="section-title">标注导出</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-annot-show">显示标注</button>';
  h += '<button class="btn btn-outline" id="btn-annot-hide">清除标注</button>';
  h += '<button class="btn btn-outline" id="btn-export">导出 PNG</button>';
  h += '</div>';
  h += '<div class="section-title">固定参照</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-set-fixed">设为固定参照</button>';
  h += '<button class="btn btn-outline" id="btn-clear-fixed">取消固定</button>';
  h += '</div>';
  h += '<div class="section-title">结构树</div>';
  h += '<div id="tree-container" style="max-height:220px;overflow-y:auto;"></div>';
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
  h += '<div class="section-title">爆炸控制</div>';
  h += '<div class="slider-row"><span>距离</span><input type="range" id="slider-dist" min="10" max="2000" value="150" step="5"><span id="val-dist">150</span>mm</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-explode">逐阶段爆炸</button>';
  h += '<button class="btn btn-outline" id="btn-explode-instant">一键爆炸</button>';
  h += '<button class="btn btn-outline" id="btn-reset">复位</button></div>';
  h += '<div class="section-title">拆卸演示</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-disassemble">逐件拆卸演示</button>';
  h += '<button class="btn btn-outline" id="btn-step">单步拆卸</button>';
  h += '<button class="btn btn-outline" id="btn-restore">复位全部</button>';
  h += '</div>';
  h += '<div class="section-title">手动移动</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-manual-on">开启拖拽</button>';
  h += '<button class="btn btn-outline" id="btn-manual-off">关闭拖拽</button></div>';
  h += '<div class="section-title">视角</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-vfront">前视</button>';
  h += '<button class="btn btn-outline" id="btn-vback">后视</button>';
  h += '<button class="btn btn-outline" id="btn-vleft">左视</button>';
  h += '<button class="btn btn-outline" id="btn-vright">右视</button>';
  h += '<button class="btn btn-outline" id="btn-vtop">俯视</button>';
  h += '<button class="btn btn-outline" id="btn-viso">等轴测</button>';
  h += '</div>';
  h += '<div class="section-title">标注导出</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-annot-show">显示标注</button>';
  h += '<button class="btn btn-outline" id="btn-annot-hide">清除标注</button>';
  h += '<button class="btn btn-outline" id="btn-thrust">推力线</button>';
  h += '<button class="btn btn-outline" id="btn-export">导出 PNG</button></div>';
  h += '<div class="section-title">固定参照</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-set-fixed">设为固定参照</button>';
  h += '<button class="btn btn-outline" id="btn-clear-fixed">取消固定</button>';
  h += '</div>';
  h += '<div class="section-title">结构树</div>';
  h += '<div id="tree-container" style="max-height:180px;overflow-y:auto;"></div>';
  panelBody.innerHTML = h;
  bindExplosionPanel();
}

function renderDisassemblyPanel() {
  let h = '';
  h += '<div class="section-title">管线进度</div>';
  h += '<div id="pipeline-log-placeholder" style="margin:4px 10px;padding:6px;background:#0a0a1a;border-radius:3px;font-family:Consolas,monospace;font-size:9px;color:#7ec8e3;max-height:120px;overflow-y:auto;"></div>';
  h += '<div class="section-title">加载</div>';
  h += '<div class="btn-group"><button class="btn btn-pri" id="btn-load">加载 JSON</button></div>';
  h += '<div class="section-title">依赖链分析</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-pipeline-chain">选中目标 → 分析拆卸链</button>';
  h += '<span id="sel-node-display" style="padding:5px;color:#7ec8e3;font-size:11px;">未选中</span>';
  h += '</div>';
  h += '<div class="section-title">全量拆装</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-pipeline-node">选中节点 → 生成拆装方案</button>';
  h += '</div>';
  h += '<div class="section-title">爆炸</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-explode">逐阶段爆炸</button>';
  h += '<button class="btn btn-outline" id="btn-reset">复位</button></div>';
  h += '<div class="section-title">拆卸演示</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-disassemble">逐件拆卸演示</button>';
  h += '<button class="btn btn-outline" id="btn-step">单步拆卸</button>';
  h += '<button class="btn btn-outline" id="btn-restore">复位全部</button>';
  h += '</div>';
  h += '<div class="section-title">导出</div>';
  h += '<div class="btn-group"><button class="btn btn-outline" id="btn-export">导出 PNG</button></div>';
  h += '<div class="section-title">固定参照</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-set-fixed">设为固定参照</button>';
  h += '<button class="btn btn-outline" id="btn-clear-fixed">取消固定</button>';
  h += '</div>';
  h += '<div class="section-title">拆装阶段</div>';
  h += '<div id="tree-container" style="max-height:180px;overflow-y:auto;"></div>';
  panelBody.innerHTML = h;
  bindDisassemblyPanel();
}

// ── Panel Event Binders ──────────────────────────────────

function _bindViewButtons() {
  document.getElementById('btn-vfront')?.addEventListener('click', () => sm.viewFront());
  document.getElementById('btn-vback')?.addEventListener('click', () => sm.viewBack());
  document.getElementById('btn-vleft')?.addEventListener('click', () => sm.viewLeft());
  document.getElementById('btn-vright')?.addEventListener('click', () => sm.viewRight());
  document.getElementById('btn-vtop')?.addEventListener('click', () => sm.viewTop());
  document.getElementById('btn-viso')?.addEventListener('click', () => sm.viewIsometric());
}

function _bindFixedButtons() {
  document.getElementById('btn-set-fixed')?.addEventListener('click', () => {
    const t = tabs[activeTab];
    if (!t.tree) { statusBar.textContent = '请先加载数据'; return; }
    const partIds = t.tree.getSelectedPartIds();
    if (!partIds || partIds.length === 0) { statusBar.textContent = '请先在结构树中选择节点'; return; }
    for (const id of partIds) shared.fixedPartIds.add(id);
    sharedExplo.setFixedPartIds(shared.fixedPartIds);
    for (const tab of tabs) {
      if (tab.tree) tab.tree.setFixedPartIds(shared.fixedPartIds);
    }
    statusBar.textContent = '已设为固定: ' + partIds.length + ' 个零件';
  });
  document.getElementById('btn-clear-fixed')?.addEventListener('click', () => {
    shared.fixedPartIds.clear();
    sharedExplo.setFixedPartIds([]);
    for (const tab of tabs) {
      if (tab.tree) tab.tree.setFixedPartIds([]);
    }
    statusBar.textContent = '已取消所有固定参照';
  });
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
    if (shared.assembly) annot.setParts(shared.assembly.parts, shared.checkedPartIds);
    annot.setHiddenPartIds(shared.hiddenPartIds);
    annot.show();
    annot.draw();
  });

  document.getElementById('btn-annot-hide')?.addEventListener('click', () => annot.clear());
  document.getElementById('btn-export')?.addEventListener('click', _exportAnnotated);
  _bindViewButtons();
  _bindFixedButtons();
  buildActiveTree();
  _renderBomList();
}

function bindExplosionPanel() {
  document.getElementById('btn-load-bom-explosion')?.addEventListener('click', async () => {
    if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
    statusBar.textContent = 'BOM 全管线分析中...';
    await window.electronAPI.runBomFullPipeline(null);
  });
  document.getElementById('btn-load')?.addEventListener('click', loadAssembly);
  const slider = document.getElementById('slider-dist');
  const val = document.getElementById('val-dist');
  slider?.addEventListener('input', () => {
    const v = parseInt(slider.value);
    val.textContent = v;
    sharedExplo.setExplosionDistance(v);
  });
  document.getElementById('btn-explode')?.addEventListener('click', () => sharedExplo.explodeGroupsAnimated(800));
  document.getElementById('btn-explode-instant')?.addEventListener('click', () => sharedExplo.explodeGroupsInstant());
  document.getElementById('btn-reset')?.addEventListener('click', () => { sharedExplo.resetPositions(); sharedExplo.hideThrustLines(); });
  document.getElementById('btn-manual-on')?.addEventListener('click', () => sharedExplo.enableManualMode());
  document.getElementById('btn-manual-off')?.addEventListener('click', () => sharedExplo.disableManualMode());
  document.getElementById('btn-annot-show')?.addEventListener('click', () => {
    if (shared.assembly) annot.setParts(shared.assembly.parts, shared.checkedPartIds);
    annot.setHiddenPartIds(shared.hiddenPartIds);
    annot.show();
    annot.draw();
  });
  document.getElementById('btn-annot-hide')?.addEventListener('click', () => annot.clear());
  document.getElementById('btn-thrust')?.addEventListener('click', () => sharedExplo.toggleThrustLines());
  document.getElementById('btn-export')?.addEventListener('click', _exportAnnotated);
  _bindViewButtons();
  _bindFixedButtons();
  _bindDisassemblyButtons();
  buildActiveTree();
}

function bindDisassemblyPanel() {
  document.getElementById('btn-load')?.addEventListener('click', loadAssembly);

  document.getElementById('btn-pipeline-chain')?.addEventListener('click', async () => {
    if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
    const targetPart = shared.selectedNode || tabs[activeTab].tree?.getCheckedNodeId();
    if (!targetPart) { statusBar.textContent = '请先在结构树中选择或勾选目标零件'; return; }
    if (!shared.bomSourcePath) { statusBar.textContent = '请先通过位置图加载BOM数据'; return; }
    statusBar.textContent = '分析拆卸依赖链: ' + targetPart + '...';
    await window.electronAPI.runBomFullPipelineCached(
      shared.bomSourcePath, shared.bomModelsDir, targetPart);
  });
  document.getElementById('btn-explode')?.addEventListener('click', () => sharedExplo.explodeGroupsAnimated(800));
  document.getElementById('btn-reset')?.addEventListener('click', () => sharedExplo.resetPositions());
  document.getElementById('btn-export')?.addEventListener('click', _exportSimple);
  document.getElementById('btn-pipeline-node')?.addEventListener('click', async () => {
    if (!window.electronAPI) { statusBar.textContent = '错误: 需在 Electron 环境中运行'; return; }
    const nodeName = shared.checkedPartIds.size > 0
      ? (tabs[activeTab].tree ? tabs[activeTab].tree.getCheckedNodeId() : null)
      : shared.selectedNode;
    if (!nodeName) { statusBar.textContent = '请先在结构树中勾选或选择一个节点'; return; }
    if (!shared.sourceStpPath) { statusBar.textContent = '请先导入 STP 预览 (Ctrl+I)'; return; }
    statusBar.textContent = '启动管线 (节点: ' + nodeName + ')...';
    await window.electronAPI.runPipelineForNodeCached(shared.sourceStpPath, nodeName);
  });
  _bindFixedButtons();
  _bindDisassemblyButtons();
  buildActiveTree();
  const display = document.getElementById('sel-node-display');
  if (display) display.textContent = shared.selectedNode || '未选中';
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
    onColorChange: (id, color) => {
      const c = new THREE.Color(color);
      for (const mesh of shared.meshes) {
        if (mesh.userData.partId === id && mesh.material) {
          const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
          for (const mat of mats) { if (mat.color) mat.color.copy(c); }
        }
      }
    },
    onCheckChange: (nodeId, partIds) => {
      shared.checkedPartIds = new Set(partIds);
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
  });
  t.tree.build(shared.hierarchy, shared.assembly.parts, shared.assembly.stages);
  t.tree.setFixedPartIds(shared.fixedPartIds);
  t.tree.setHiddenPartIds(shared.hiddenPartIds);
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
  shared.fixedPartIds = new Set();
  shared.selectedNode = null;
  shared.bomEntries = [];
  _highlightedParts.length = 0;
  if (sharedExplo && typeof sharedExplo._clearGroups === 'function') sharedExplo._clearGroups();
  if (annot && typeof annot.clear === 'function') annot.clear();

  shared.assembly = assembly;
  shared.loaded = new Map();
  shared.meshes = [];

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
      shared.meshes.push(m);
      meshCount++;
      if (!m.material) {
        m.material = new THREE.MeshStandardMaterial({ color: p.color || 0xbbbbbb, roughness: 0.5, metalness: 0.0 });
      } else if (Array.isArray(m.material)) {
        for (const mat of m.material) {
          if (mat.color && mat.color.getHex() === 0xffffff && !p.color) mat.color.set(0xbbbbbb);
        }
      } else if (m.material.color && m.material.color.getHex() === 0xffffff && !p.color) {
        m.material.color.set(0xbbbbbb);
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
    _highlightPart(partId);
    statusBar.textContent = '选中: ' + partId;

    const t = tabs[activeTab];
    if (t.tree) {
      const sel = t.tree.container.querySelector('[data-node-id="' + partId + '"]');
      if (sel) {
        if (t.tree.selected) t.tree.selected.classList.remove('selected');
        t.tree.selected = sel;
        sel.classList.add('selected');
      }
    }
  };

  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointerup', onUp);
  _viewportPointerHandler = { down: onDown, up: onUp };
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
  window.electronAPI.onPipelineProgress((msg) => _logPipeline(msg));
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
  });
  // Serialize concurrent pipeline-complete events so double-clicks don't race.
  let _loadingChain = Promise.resolve();
  window.electronAPI.onPipelineComplete((jsonPath) => {
    _loadingChain = _loadingChain.then(async () => {
      try {
        _logPipeline('完成! ' + jsonPath);
        const targetIdx = (pipelineMode === 'full' || pipelineMode === 'bom-full') ? 2 : activeTab;
        await _loadPipelineResult(jsonPath, targetIdx);
        _renderBomList();
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
statusBar.textContent = '就绪 — Ctrl+B 加载BOM | Ctrl+O 加载JSON';
