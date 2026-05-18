/**
 * 整车数模自动拆装方案系统 — Electron 桌面应用
 *
 * 单一场景架构：一个视口 + 一个渲染器 + 一个场景。
 * 三个 Tab 共享同一份模型数据（shared），各自维护独立的爆炸/树状态。
 */

import * as THREE from 'three';
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
const sm = new SceneManager(viewport, { backgroundColor: 0xe8ecf0 });
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
};

// ── Per-tab state ─────────────────────────────────────────
const tabs = [
  { explo: null, tree: null },
  { explo: null, tree: null },
  { explo: null, tree: null },
];

tabs.forEach((t, i) => {
  t.explo = new ExplosionView(sm.scene, sm.camera, sm.renderer.domElement, sm.controls);
  t.explo.onStatus((msg) => { if (activeTab === i) statusBar.textContent = msg; });
  t.explo.onClearHighlight = () => { _clearHighlight(); statusBar.textContent = '就绪'; };
});

// ── Tab Switching ────────────────────────────────────────
tabBtns.forEach((btn) => {
  btn.addEventListener('click', () => switchTab(parseInt(btn.dataset.tab)));
});

function switchTab(idx) {
  if (idx === activeTab) return;

  const prev = tabs[activeTab];
  prev.explo.disableManualMode();
  prev.explo.hideThrustLines();

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

function renderPositionPanel() {
  let h = '';
  h += '<div class="section-title">车壳选择</div>';
  h += '<select class="sel" id="sel-body">';
  for (const b of bodyLoader.bodies) {
    h += '<option value="' + b.name + '">' + b.name + '</option>';
  }
  h += '</select>';
  h += '<div class="section-title">目标部件</div>';
  h += '<div class="btn-group"><button class="btn btn-pri" id="btn-load">加载装配数据</button></div>';
  h += '<div class="section-title">标注导出</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-outline" id="btn-annot-show">显示标注</button>';
  h += '<button class="btn btn-outline" id="btn-annot-hide">清除标注</button>';
  h += '<button class="btn btn-outline" id="btn-export">导出 PNG</button>';
  h += '</div>';
  h += '<div class="section-title">结构树 (点击色块改颜色)</div>';
  h += '<div id="tree-container" style="max-height:260px;overflow-y:auto;"></div>';
  panelBody.innerHTML = h;
  bindPositionPanel();
}

function renderExplosionPanel() {
  let h = '';
  h += '<div class="section-title">目标部件</div>';
  h += '<div class="btn-group"><button class="btn btn-pri" id="btn-load">加载装配数据</button></div>';
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
  h += '<button class="btn btn-outline" id="btn-export">导出 PNG</button></div>';
  h += '<div class="section-title">结构树</div>';
  h += '<div id="tree-container" style="max-height:200px;overflow-y:auto;"></div>';
  panelBody.innerHTML = h;
  bindExplosionPanel();
}

function renderDisassemblyPanel() {
  let h = '';
  h += '<div class="section-title">管线进度</div>';
  h += '<div id="pipeline-log-placeholder" style="margin:4px 10px;padding:6px;background:#0a0a1a;border-radius:3px;font-family:Consolas,monospace;font-size:9px;color:#7ec8e3;max-height:120px;overflow-y:auto;"></div>';
  h += '<div class="section-title">加载</div>';
  h += '<div class="btn-group"><button class="btn btn-pri" id="btn-load">加载装配数据</button></div>';
  h += '<div class="section-title">爆炸</div>';
  h += '<div class="btn-group">';
  h += '<button class="btn btn-pri" id="btn-explode">逐阶段爆炸</button>';
  h += '<button class="btn btn-outline" id="btn-reset">复位</button></div>';
  h += '<div class="section-title">导出</div>';
  h += '<div class="btn-group"><button class="btn btn-outline" id="btn-export">导出 PNG</button></div>';
  h += '<div class="section-title">拆装阶段</div>';
  h += '<div id="tree-container" style="max-height:200px;overflow-y:auto;"></div>';
  panelBody.innerHTML = h;
  bindDisassemblyPanel();
}

// ── Panel Event Binders ──────────────────────────────────

function bindPositionPanel() {
  document.getElementById('sel-body')?.addEventListener('change', async (e) => {
    await bodyLoader.switchBody(e.target.selectedIndex, sm.scene);
  });
  document.getElementById('btn-load')?.addEventListener('click', loadAssembly);
  document.getElementById('btn-annot-show')?.addEventListener('click', () => {
    if (shared.assembly) annot.setParts(shared.assembly.parts);
    annot.show();
  });
  document.getElementById('btn-annot-hide')?.addEventListener('click', () => annot.clear());
  document.getElementById('btn-export')?.addEventListener('click', _exportAnnotated);
  buildActiveTree();
}

function bindExplosionPanel() {
  document.getElementById('btn-load')?.addEventListener('click', loadAssembly);
  const slider = document.getElementById('slider-dist');
  const val = document.getElementById('val-dist');
  slider?.addEventListener('input', () => {
    const v = parseInt(slider.value);
    val.textContent = v;
    tabs[activeTab].explo.setExplosionDistance(v);
  });
  document.getElementById('btn-explode')?.addEventListener('click', () => tabs[activeTab].explo.explodeGroupsAnimated(800));
  document.getElementById('btn-explode-instant')?.addEventListener('click', () => tabs[activeTab].explo.explodeGroupsInstant());
  document.getElementById('btn-reset')?.addEventListener('click', () => { tabs[activeTab].explo.resetPositions(); tabs[activeTab].explo.hideThrustLines(); });
  document.getElementById('btn-manual-on')?.addEventListener('click', () => tabs[activeTab].explo.enableManualMode());
  document.getElementById('btn-manual-off')?.addEventListener('click', () => tabs[activeTab].explo.disableManualMode());
  document.getElementById('btn-annot-show')?.addEventListener('click', () => {
    if (shared.assembly) annot.setParts(shared.assembly.parts);
    annot.show();
  });
  document.getElementById('btn-annot-hide')?.addEventListener('click', () => annot.clear());
  document.getElementById('btn-thrust')?.addEventListener('click', () => tabs[activeTab].explo.toggleThrustLines());
  document.getElementById('btn-export')?.addEventListener('click', _exportAnnotated);
  buildActiveTree();
}

function bindDisassemblyPanel() {
  document.getElementById('btn-load')?.addEventListener('click', loadAssembly);
  document.getElementById('btn-explode')?.addEventListener('click', () => tabs[activeTab].explo.explodeGroupsAnimated(800));
  document.getElementById('btn-reset')?.addEventListener('click', () => tabs[activeTab].explo.resetPositions());
  document.getElementById('btn-export')?.addEventListener('click', _exportSimple);
  buildActiveTree();
}

function buildActiveTree() {
  const container = document.getElementById('tree-container');
  if (!container || !shared.assembly) return;
  const t = tabs[activeTab];
  t.tree = new TreeView(container, {
    onSelect: (id) => {
      _highlightPart(id);
      for (const mesh of shared.meshes) {
        if (mesh.userData.partId === id) {
          const box = new THREE.Box3().setFromObject(mesh);
          const c = new THREE.Vector3(); box.getCenter(c);
          sm.focusOn(c, 300); break;
        }
      }
      statusBar.textContent = '选中: ' + id;
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
  });
  t.tree.build(shared.assembly.parts, shared.assembly.stages);
}

// ── Load Assembly ────────────────────────────────────────

function _glbPath(dir, glbFile) {
  if (glbFile.indexOf('/') === -1 && glbFile.indexOf('\\') === -1) {
    return dir + '/parts/' + glbFile;
  }
  return dir + '/' + glbFile;
}

async function _loadModelCore(assembly, dir) {
  // Clear previous models from scene
  if (shared.loaded) {
    for (const [, p] of shared.loaded) {
      if (p.modelData && p.modelData.scene) sm.scene.remove(p.modelData.scene);
    }
  }
  shared.assembly = assembly;
  shared.loaded = new Map();
  shared.meshes = [];

  let meshCount = 0;
  for (const part of assembly.parts) {
    const glbPath = _glbPath(dir, part.glbFile);
    if (!(await window.electronAPI.fileExists(glbPath))) continue;
    const url = 'local:///' + glbPath.replace(/\\/g, '/');
    try {
      const data = await modelLoader.loadModel(url);
      shared.loaded.set(part.id, { ...part, modelData: data, meshes: data.meshes });
    } catch (e) { console.error(e); }
  }

  for (const [, p] of shared.loaded) {
    for (const m of p.meshes) {
      m.userData.partId = p.id;
      shared.meshes.push(m);
      meshCount++;
      if (!m.material) {
        m.material = new THREE.MeshStandardMaterial({ color: p.color || 0x8899aa, roughness: 0.6, metalness: 0.1 });
      } else if (Array.isArray(m.material)) {
        for (const mat of m.material) {
          if (mat.color && mat.color.getHex() === 0xffffff && !p.color) mat.color.set(0x8899aa);
        }
      } else if (m.material.color && m.material.color.getHex() === 0xffffff && !p.color) {
        m.material.color.set(0x8899aa);
      }
    }
    if (p.modelData && p.modelData.scene) sm.scene.add(p.modelData.scene);
  }

  shared.groups = AssemblyLoader._buildGroups(assembly, shared.loaded);
  for (const t of tabs) t.explo.loadAssemblyGroups(shared.groups);

  _logPipeline('Loaded ' + shared.loaded.size + ' parts, ' + meshCount + ' meshes');
  _logPipeline('Scene children: ' + sm.scene.children.length);

  buildActiveTree();
  _focusCamera();
  _setupViewportClick();
}

function _focusCamera() {
  sm.scene.updateMatrixWorld();
  const bbox = sm.getSceneBBox();
  _logPipeline('BBox empty: ' + bbox.isEmpty());
  if (!bbox.isEmpty()) {
    const center = new THREE.Vector3(); bbox.getCenter(center);
    const size = new THREE.Vector3(); bbox.getSize(size);
    const diagonal = Math.sqrt(size.x * size.x + size.y * size.y + size.z * size.z);
    _logPipeline('BBox size: ' + size.x.toFixed(0) + ', ' + size.y.toFixed(0) + ', ' + size.z.toFixed(0));
    _logPipeline('Diagonal: ' + diagonal.toFixed(0));
    const focusDist = Math.max(2500, diagonal * 0.65);
    _logPipeline('Focus distance: ' + focusDist.toFixed(0));
    sm.focusOn(center, focusDist);
  } else {
    _logPipeline('WARNING: empty bbox, using default view');
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
  _clearHighlight();
  const targetMeshes = shared.meshes.filter(m => m.userData.partId === partId);
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
      const sel = t.tree.container.querySelector('[data-part-id="' + partId + '"]');
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
  window.electronAPI.onPipelineStarted(() => {
    const log = getPipelineLog();
    if (log) log.innerHTML = '';
    _logPipeline('管线启动...');
    if (pipelineMode === 'full') switchTab(2);
  });
  window.electronAPI.onPipelineComplete(async (jsonPath) => {
    _logPipeline('完成! ' + jsonPath);
    const targetIdx = pipelineMode === 'full' ? 2 : activeTab;
    await _loadPipelineResult(jsonPath, targetIdx);
  });
}

// ── Electron Menu Events ─────────────────────────────────
if (window.electronAPI) {
  window.electronAPI.onMenuLoadAssembly(async () => { await loadAssembly(); });
  window.electronAPI.onMenuResetCamera(() => sm.resetCamera());
  window.electronAPI.onMenuScreenshot(() => _exportAnnotated());
}

// ── Startup ──────────────────────────────────────────────
bodyLoader.loadManifest('bodies/manifest.json', modelLoader).then(() => renderPanel(0));
sm.renderer.domElement.style.display = 'block';
statusBar.textContent = '就绪 — Ctrl+O 加载数据 | Ctrl+I 导入 STP 预览';
