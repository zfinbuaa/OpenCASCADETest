/**
 * Explosion View — 爆炸动画 + TransformControls 手动拖拽。
 */

import * as THREE from 'three';
import { TransformControls } from 'three/addons/controls/TransformControls.js';

const _CLICK_THRESHOLD = 5;

export class ExplosionView {

  /**
   * @param {THREE.Scene} scene
   * @param {THREE.Camera} camera
   * @param {HTMLElement} domElement - renderer.domElement
   * @param {THREE.OrbitControls} orbitControls - for disable during drag
   */
  constructor(scene, camera, domElement, orbitControls) {
    this.scene = scene;
    this.camera = camera;
    this.domElement = domElement;
    this._orbitControls = orbitControls || null;
    this.assemblyGroups = [];
    this.meshToGroup = new Map();
    this.explodedPositions = new Map();
    this.originalPositions = new Map();
    this.explosionDistance = 150;
    this.isExploded = false;
    this._statusCallback = null;

    this._thrustLines = [];
    this._thrustVisible = false;

    this.onClearHighlight = null;

    this._transformCtrl = new TransformControls(camera, domElement);
    this._transformCtrl.enabled = false;
    this._transformCtrl.addEventListener('dragging-changed', (e) => {
      if (e.value) {
        if (this._orbitControls) this._orbitControls.enabled = false;
      } else {
        if (this._orbitControls) this._orbitControls.enabled = true;
        if (this._selectedMesh) {
          this.explodedPositions.set(this._selectedMesh,
            this._selectedMesh.position.clone());
        }
      }
    });
    scene.add(this._transformCtrl);

    this._selectedMesh = null;
    this._pointerDownPos = null;
    this._onPointerDownBinded = this._onPointerDown.bind(this);
    this._onPointerUpBinded = this._onPointerUp.bind(this);
  }

  onStatus(cb) { this._statusCallback = cb; }
  _setStatus(msg) {
    if (this._statusCallback) this._statusCallback(msg);
  }

  // ── Group Management ────────────────────────────

  loadAssemblyGroups(groups) {
    this._clearGroups();
    this.assemblyGroups = groups;
    for (const g of groups) {
      for (const mesh of g.meshes) {
        this.meshToGroup.set(mesh, g.id);
        this.originalPositions.set(mesh, mesh.position.clone());
        this.explodedPositions.set(mesh, mesh.position.clone());
      }
    }
    this._setStatus('已加载 ' + groups.length + ' 个装配编组');
  }

  _clearGroups() {
    this._disableTransformCtrl();
    this.clearThrustLines();
    this.assemblyGroups = [];
    this.meshToGroup.clear();
    this.originalPositions.clear();
    this.explodedPositions.clear();
  }

  // ── Direction ───────────────────────────────────

  _directionToVector(direction) {
    if (Array.isArray(direction) && direction.length === 3) {
      return new THREE.Vector3(direction[0], direction[1], direction[2]);
    }
    const dirStr = String(direction).toUpperCase();
    const axes = {
      '+X': [1, 0, 0], 'X': [1, 0, 0], '-X': [-1, 0, 0],
      '+Y': [0, 1, 0], 'Y': [0, 1, 0], '-Y': [0, -1, 0],
      '+Z': [0, 0, 1], 'Z': [0, 0, 1], '-Z': [0, 0, -1],
    };
    if (axes[dirStr]) return new THREE.Vector3(...axes[dirStr]);
    const parts = dirStr.split(',').map(Number);
    if (parts.length === 3 && parts.every(n => !isNaN(n))) {
      return new THREE.Vector3(parts[0], parts[1], parts[2]);
    }
    return new THREE.Vector3(0, 1, 0);
  }

  // ── Explode / Reset ─────────────────────────────

  async explodeGroupsAnimated(duration = 600) {
    if (this.assemblyGroups.length === 0) return;
    this.resetPositions();
    const stageMap = new Map();
    for (const g of this.assemblyGroups) {
      const s = g.stage || 1;
      if (!stageMap.has(s)) stageMap.set(s, []);
      stageMap.get(s).push(g);
    }
    const stages = Array.from(stageMap.keys()).sort((a, b) => a - b);
    for (const stage of stages) {
      this._setStatus('拆卸阶段 ' + stage + ' / ' + stages.length);
      const groups = stageMap.get(stage);
      const targets = new Map();
      for (const group of groups) {
        const dir = this._directionToVector(group.direction);
        const dist = this.explosionDistance * (group.distanceMultiplier || 1);
        for (const mesh of group.meshes) {
          const origin = this.explodedPositions.get(mesh).clone();
          const target = origin.clone().add(dir.clone().multiplyScalar(dist));
          targets.set(mesh, target);
        }
      }
      await this._animateToPositions(targets, duration / stages.length);
      await new Promise(r => setTimeout(r, 300));
    }
    this.isExploded = true;
    this._setStatus('爆炸完成');
  }

  explodeGroupsInstant() {
    for (const g of this.assemblyGroups) {
      const dir = this._directionToVector(g.direction);
      const dist = this.explosionDistance * (g.distanceMultiplier || 1);
      for (const mesh of g.meshes) {
        const origin = this.originalPositions.get(mesh).clone();
        const target = origin.clone().add(dir.clone().multiplyScalar(dist));
        mesh.position.copy(target);
        this.explodedPositions.set(mesh, target.clone());
      }
    }
    this.isExploded = true;
    this._setStatus('一键爆炸完成');
  }

  resetPositions() {
    for (const [mesh, pos] of this.originalPositions) {
      mesh.position.copy(pos);
      this.explodedPositions.set(mesh, pos.clone());
    }
    this.isExploded = false;
    this._setStatus('已复位');
  }

  _animateToPositions(targets, duration) {
    return new Promise((resolve) => {
      const startTime = performance.now();
      const startPositions = new Map();
      for (const [mesh, tgt] of targets) {
        startPositions.set(mesh, this.explodedPositions.get(mesh).clone());
      }
      const animate = () => {
        const elapsed = performance.now() - startTime;
        const t = Math.min(elapsed / Math.max(duration, 1), 1);
        const eased = 1 - Math.pow(1 - t, 3);
        for (const [mesh, target] of targets) {
          const start = startPositions.get(mesh);
          const current = new THREE.Vector3().lerpVectors(start, target, eased);
          this.explodedPositions.set(mesh, current.clone());
          mesh.position.copy(current);
        }
        if (t < 1) requestAnimationFrame(animate);
        else resolve();
      };
      requestAnimationFrame(animate);
    });
  }

  setExplosionDistance(dist) { this.explosionDistance = dist; }

  // ── Manual Move (TransformControls) ─────────────

  /** 启用手动拖拽模式 */
  enableManualMode() {
    this._transformCtrl.enabled = true;
    this._setStatus('手动模式 — 点击零件拖拽');
    this.domElement.style.cursor = 'pointer';
    this.domElement.addEventListener('pointerdown', this._onPointerDownBinded);
    this.domElement.addEventListener('pointerup', this._onPointerUpBinded);
  }

  /** 禁用手动拖拽模式 */
  disableManualMode() {
    this._transformCtrl.enabled = false;
    this._transformCtrl.detach();
    this._selectedMesh = null;
    this.domElement.style.cursor = '';
    this.domElement.removeEventListener('pointerdown', this._onPointerDownBinded);
    this.domElement.removeEventListener('pointerup', this._onPointerUpBinded);
    this._pointerDownPos = null;
    this._setStatus('手动模式已关闭');
  }

  _onPointerDown(event) {
    this._pointerDownPos = { x: event.clientX, y: event.clientY };
  }

  _onPointerUp(event) {
    if (!this._pointerDownPos) return;
    const dx = event.clientX - this._pointerDownPos.x;
    const dy = event.clientY - this._pointerDownPos.y;
    this._pointerDownPos = null;
    if (dx * dx + dy * dy > _CLICK_THRESHOLD * _CLICK_THRESHOLD) return;

    const rect = this.domElement.getBoundingClientRect();
    const mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );

    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(mouse, this.camera);

    const meshes = [];
    this.scene.traverse(c => { if (c.isMesh) meshes.push(c); });
    const intersects = raycaster.intersectObjects(meshes, false);

    if (intersects.length > 0) {
      const mesh = intersects[0].object;
      if (this.meshToGroup.has(mesh)) {
        this._selectForTransform(mesh);
      }
    } else {
      if (this.onClearHighlight) this.onClearHighlight();
    }
  }

  _selectForTransform(mesh) {
    this._selectedMesh = mesh;
    this._transformCtrl.attach(mesh);
    this._setStatus('拖拽: ' + (mesh.userData.partId || 'unknown'));
  }

  _disableTransformCtrl() {
    this._transformCtrl.enabled = false;
    this._transformCtrl.detach();
    this._selectedMesh = null;
    this.domElement.style.cursor = '';
    this.domElement.removeEventListener('pointerdown', this._onPointerDownBinded);
    this.domElement.removeEventListener('pointerup', this._onPointerUpBinded);
    this._pointerDownPos = null;
  }

  // ── Thrust Lines ────────────────────────────────

  showThrustLines() {
    this.clearThrustLines();
    for (const [mesh, orig] of this.originalPositions) {
      if (!this.isExploded) break;
      const exploded = this.explodedPositions.get(mesh);
      if (!exploded || exploded.equals(orig)) continue;

      const geom = new THREE.BufferGeometry().setFromPoints([orig, exploded]);
      const mat = new THREE.LineDashedMaterial({
        color: 0xff4444, dashSize: 8, gapSize: 4, linewidth: 1,
      });
      const line = new THREE.Line(geom, mat);
      line.computeLineDistances();
      this.scene.add(line);
      this._thrustLines.push(line);
    }
    this._thrustVisible = true;
  }

  hideThrustLines() {
    this.clearThrustLines();
  }

  clearThrustLines() {
    for (const line of this._thrustLines) {
      this.scene.remove(line);
    }
    this._thrustLines = [];
    this._thrustVisible = false;
  }

  toggleThrustLines() {
    if (this._thrustVisible) this.hideThrustLines();
    else this.showThrustLines();
  }

  dispose() {
    this._disableTransformCtrl();
    this.clearThrustLines();
    this.scene.remove(this._transformCtrl);
    this._transformCtrl.dispose();
  }
}
