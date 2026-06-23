/**
 * Explosion View — 爆炸动画 + TransformControls 手动拖拽 + 固定参照物。
 */

import * as THREE from '../node_modules/three/build/three.module.js';
import { TransformControls } from './three-addons/controls/TransformControls.js';

const _CLICK_THRESHOLD = 5;

export class ExplosionView {

  constructor(scene, camera, domElement, orbitControls) {
    this.scene = scene;
    this.camera = camera;
    this.domElement = domElement;
    this._orbitControls = orbitControls || null;
    this.assemblyGroups = [];
    this.meshToGroup = new Map();
    this.explodedPositions = new Map();
    this.originalPositions = new Map();
    this.originalLocalPositions = new Map();
    this.explosionDistance = 150;
    this.isExploded = false;
    this._statusCallback = null;
    this._fixedPartIds = new Set();
    this._ghostMeshes = new Map();
    this._matSnapshot = new Map();
    this._centerMeshes = new Set();
    this._centerEmissive = new Map();

    this._thrustLines = [];
    this._thrustVisible = false;

    this._removedMeshes = new Map();
    this._disassembling = false;
    this._disassembleStage = 0;
    this._pathLines = [];

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
            this._selectedMesh.getWorldPosition(new THREE.Vector3()));
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

  // ── Fixed Reference ─────────────────────────────

  setFixedPartIds(ids) {
    const oldIds = new Set(this._fixedPartIds);
    this._fixedPartIds = new Set(ids);

    for (const [mesh] of this.originalPositions) {
      const partId = mesh.userData.partId;
      const wasFixed = oldIds.has(partId);
      const isFixed = this._fixedPartIds.has(partId);
      if (wasFixed !== isFixed) {
        this._setMeshGhost(mesh, isFixed);
      }
    }

    const count = this._fixedPartIds.size;
    this._setStatus(count > 0 ? count + ' 个零件已设为固定' : '已取消所有固定');
  }

  getFixedPartIds() {
    return new Set(this._fixedPartIds);
  }

  _isFixedMesh(mesh) {
    return this._fixedPartIds.has(mesh.userData.partId);
  }

  _isExplosionCenter(mesh) {
    return !!(mesh && mesh.userData && mesh.userData.isExplosionCenter);
  }

  _setMeshCenter(mesh) {
    if (!mesh || !mesh.material) return;
    if (this._centerMeshes.has(mesh)) return;
    this._centerMeshes.add(mesh);
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const mat of mats) {
      if (mat.emissive) {
        this._centerEmissive.set(mat, {
          color: mat.emissive.clone(),
          intensity: mat.emissiveIntensity !== undefined ? mat.emissiveIntensity : 0,
        });
        mat.emissive.setHex(0xffaa00);
        mat.emissiveIntensity = 0.4;
        mat.needsUpdate = true;
      }
    }
  }

  _clearCenterMeshes() {
    for (const mesh of this._centerMeshes) {
      if (!mesh.material) continue;
      const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      for (const mat of mats) {
        const saved = this._centerEmissive.get(mat);
        if (saved && mat.emissive) {
          mat.emissive.copy(saved.color);
          mat.emissiveIntensity = saved.intensity;
          mat.needsUpdate = true;
        }
      }
    }
    this._centerMeshes.clear();
    this._centerEmissive.clear();
  }

  _applyWorldOffset(mesh, worldOffset) {
    if (mesh.parent) {
      mesh.parent.updateMatrixWorld(true);
      const parentInv = new THREE.Matrix4().copy(mesh.parent.matrixWorld).invert();
      const localOffset = worldOffset.clone().transformDirection(parentInv);
      mesh.position.add(localOffset);
    } else {
      mesh.position.add(worldOffset);
    }
  }

  // ── Material State Snapshot ────────────────────
  // Snapshot before any mutation, restore from snapshot. This avoids
  // the bug where fade-out's mutation gets recorded as the "original" state.

  _saveMaterialState(mesh) {
    if (!mesh.material) return;
    if (this._matSnapshot.has(mesh)) return;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    this._matSnapshot.set(mesh, mats.map(m => ({
      opacity: m.opacity,
      transparent: m.transparent,
      depthWrite: m.depthWrite,
    })));
  }

  _restoreMaterialState(mesh) {
    const snap = this._matSnapshot.get(mesh);
    if (!snap || !mesh.material) return;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (let i = 0; i < mats.length; i++) {
      const s = snap[i];
      if (!s) continue;
      mats[i].opacity = s.opacity;
      mats[i].transparent = s.transparent;
      mats[i].depthWrite = s.depthWrite;
      mats[i].needsUpdate = true;
    }
    this._matSnapshot.delete(mesh);
  }

  _setMeshGhost(mesh, ghost) {
    if (!mesh.material) return;
    const isGhosted = this._ghostMeshes.has(mesh);
    if (isGhosted === ghost) return;

    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];

    if (ghost) {
      // Save before mutation
      this._saveMaterialState(mesh);
      this._ghostMeshes.set(mesh, true);
      for (const mat of mats) {
        mat.transparent = true;
        mat.opacity = 0.25;
        mat.depthWrite = false;
        mat.needsUpdate = true;
      }
    } else {
      // Restore from snapshot
      this._restoreMaterialState(mesh);
      this._ghostMeshes.delete(mesh);
    }
  }

  // ── Group Management ────────────────────────────

  loadAssemblyGroups(groups) {
    this._clearGroups();
    this.assemblyGroups = groups;
    this.scene.updateMatrixWorld(true);
    for (const g of groups) {
      for (const mesh of g.meshes) {
        this.meshToGroup.set(mesh, g.id);
        const worldPos = mesh.getWorldPosition(new THREE.Vector3());
        this.originalPositions.set(mesh, worldPos.clone());
        this.explodedPositions.set(mesh, worldPos.clone());
        this.originalLocalPositions.set(mesh, mesh.position.clone());
        if (this._isFixedMesh(mesh)) {
          this._setMeshGhost(mesh, true);
        } else if (this._isExplosionCenter(mesh)) {
          this._setMeshCenter(mesh);
        }
      }
    }
    this._setStatus('已加载 ' + groups.length + ' 个装配编组');
  }

  _clearGroups() {
    this._disableTransformCtrl();
    this.clearThrustLines();
    this._clearPathLines();
    this._clearCenterMeshes();
    this.assemblyGroups = [];
    this.meshToGroup.clear();
    this.originalPositions.clear();
    this.originalLocalPositions.clear();
    this.explodedPositions.clear();
    this._ghostMeshes.clear();
    this._matSnapshot.clear();
    this._removedMeshes.clear();
    this._disassembling = false;
    this._disassembleStage = 0;
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
          if (this._isFixedMesh(mesh)) continue;
          if (this._isExplosionCenter(mesh)) continue;
          if (!mesh.visible) continue;
          const sp = this.explodedPositions.get(mesh);
          if (!sp) continue;
          const origin = sp.clone();
          const target = origin.clone().add(dir.clone().multiplyScalar(dist));
          targets.set(mesh, target);
        }
      }
      if (targets.size > 0) {
        await this._animateToPositions(targets, duration / stages.length);
        await new Promise(r => setTimeout(r, 300));
      }
    }
    this.isExploded = true;
    this._setStatus('爆炸完成');
  }

  explodeGroupsInstant() {
    for (const g of this.assemblyGroups) {
      const dir = this._directionToVector(g.direction);
      const dist = this.explosionDistance * (g.distanceMultiplier || 1);
      const worldOffset = dir.clone().multiplyScalar(dist);
      for (const mesh of g.meshes) {
        if (this._isFixedMesh(mesh)) continue;
        if (this._isExplosionCenter(mesh)) continue;
        if (!mesh.visible) continue;
        const origin = this.originalPositions.get(mesh).clone();
        const target = origin.clone().add(worldOffset);
        this._applyWorldOffset(mesh, worldOffset);
        this.explodedPositions.set(mesh, target.clone());
      }
    }
    this.isExploded = true;
    this._setStatus('一键爆炸完成');
  }

  resetPositions() {
    for (const [mesh, pos] of this.originalPositions) {
      if (this._isFixedMesh(mesh)) continue;
      if (this._isExplosionCenter(mesh)) continue;
      mesh.position.copy(this.originalLocalPositions.get(mesh) || pos);
      this.explodedPositions.set(mesh, pos.clone());
    }
    this.isExploded = false;
    this._setStatus('已复位');
  }

  _animateToPositions(targets, duration) {
    return new Promise((resolve) => {
      const startTime = performance.now();
      const startPositions = new Map();
      for (const [mesh] of targets) {
        startPositions.set(mesh, this.explodedPositions.get(mesh).clone());
      }
      const animate = () => {
        const elapsed = performance.now() - startTime;
        const t = Math.min(elapsed / Math.max(duration, 1), 1);
        const eased = 1 - Math.pow(1 - t, 3);
        for (const [mesh, target] of targets) {
          const start = startPositions.get(mesh);
          const current = new THREE.Vector3().lerpVectors(start, target, eased);
          const prev = this.explodedPositions.get(mesh);
          const worldOffset = current.clone().sub(prev);
          this._applyWorldOffset(mesh, worldOffset);
          this.explodedPositions.set(mesh, current.clone());
        }
        if (t < 1) requestAnimationFrame(animate);
        else resolve();
      };
      requestAnimationFrame(animate);
    });
  }

  setExplosionDistance(dist) { this.explosionDistance = dist; }

  // ── Manual Move (TransformControls) ─────────────

  enableManualMode() {
    this._transformCtrl.enabled = true;
    this._setStatus('手动模式 — 点击零件拖拽');
    this.domElement.style.cursor = 'pointer';
    this.domElement.addEventListener('pointerdown', this._onPointerDownBinded);
    this.domElement.addEventListener('pointerup', this._onPointerUpBinded);
  }

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
      if (this._isFixedMesh(mesh)) return;
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
      if (this._isFixedMesh(mesh)) continue;
      if (this._isExplosionCenter(mesh)) continue;
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
      if (line.geometry) line.geometry.dispose();
      if (line.material) line.material.dispose();
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
    this._clearPathLines();
    this.scene.remove(this._transformCtrl);
    this._transformCtrl.dispose();
  }

  _clearPathLines() {
    for (const line of this._pathLines) {
      this.scene.remove(line);
      if (line.geometry) line.geometry.dispose();
      if (line.material) line.material.dispose();
    }
    this._pathLines = [];
  }

  _getMeshOpacity(mesh) {
    if (!mesh.material) return 1.0;
    if (Array.isArray(mesh.material)) return mesh.material[0].opacity;
    return mesh.material.opacity;
  }

  _getMeshTransparent(mesh) {
    if (!mesh.material) return false;
    if (Array.isArray(mesh.material)) return mesh.material[0].transparent;
    return mesh.material.transparent;
  }

  _getMeshDepthWrite(mesh) {
    if (!mesh.material) return true;
    if (Array.isArray(mesh.material)) return mesh.material[0].depthWrite;
    return mesh.material.depthWrite;
  }

  _setMeshOpacity(mesh, value, transparent) {
    if (!mesh.material) return;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const mat of mats) {
      mat.opacity = value;
      if (transparent !== undefined) mat.transparent = transparent;
      mat.depthWrite = value > 0.01;
      mat.needsUpdate = true;
    }
  }

  _showRemovalPath(meshes, dirVector, distance) {
    for (const mesh of meshes) {
      if (this._isFixedMesh(mesh)) continue;
      if (this._isExplosionCenter(mesh)) continue;
      const origin = this.originalPositions.get(mesh);
      if (!origin) continue;
      const target = origin.clone().add(dirVector.clone().multiplyScalar(distance));

      const geom = new THREE.BufferGeometry().setFromPoints([origin, target]);
      const mat = new THREE.LineDashedMaterial({
        color: 0x44ff44, dashSize: 6, gapSize: 3, linewidth: 1,
      });
      const line = new THREE.Line(geom, mat);
      line.computeLineDistances();
      this.scene.add(line);
      this._pathLines.push(line);
    }
  }

  async _animateRemoval(meshes, dirVector, distance, duration) {
    const targets = new Map();
    for (const mesh of meshes) {
      if (this._isFixedMesh(mesh)) continue;
      if (this._isExplosionCenter(mesh)) continue;
      const origin = this.explodedPositions.get(mesh);
      if (!origin) continue;
      const target = origin.clone().add(dirVector.clone().multiplyScalar(distance));
      targets.set(mesh, target);
    }
    if (targets.size === 0) return;
    await this._animateToPositions(targets, duration);
    this._showRemovalPath(meshes, dirVector, distance);
  }

  async _fadeOut(meshes, duration = 400) {
    const startTime = performance.now();
    const startOpacities = new Map();

    for (const mesh of meshes) {
      if (this._isFixedMesh(mesh)) continue;
      if (this._isExplosionCenter(mesh)) continue;
      if (!mesh.material) continue;
      // Save material state before mutating
      this._saveMaterialState(mesh);
      const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      startOpacities.set(mesh, mats.map(m => m.opacity));
      for (const mat of mats) {
        mat.transparent = true;
        mat.needsUpdate = true;
      }
    }

    if (startOpacities.size === 0) return;

    return new Promise((resolve) => {
      const animate = () => {
        const elapsed = performance.now() - startTime;
        const t = Math.min(elapsed / Math.max(duration, 1), 1);
        for (const [mesh, opacities] of startOpacities) {
          const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
          for (let i = 0; i < mats.length; i++) {
            mats[i].opacity = opacities[i] * (1 - t);
            mats[i].needsUpdate = true;
          }
        }
        if (t < 1) requestAnimationFrame(animate);
        else resolve();
      };
      requestAnimationFrame(animate);
    });
  }

  _removeMeshesFromScene(meshes, preFadeOpacities) {
    for (const mesh of meshes) {
      if (this._isFixedMesh(mesh)) continue;
      if (this._isExplosionCenter(mesh)) continue;
      if (this._removedMeshes.has(mesh)) continue;
      const parent = mesh.parent;
      if (!parent) continue;

      const origPos = this.originalPositions.get(mesh);
      const savedOpacity = preFadeOpacities
        ? preFadeOpacities.get(mesh)
        : this._getMeshOpacity(mesh);

      this._removedMeshes.set(mesh, {
        parent: parent,
        position: origPos ? origPos.clone() : mesh.position.clone(),
        localPosition: mesh.position.clone(),
        opacity: savedOpacity !== undefined ? savedOpacity : 1.0,
        transparent: this._getMeshTransparent(mesh),
        depthWrite: this._getMeshDepthWrite(mesh),
      });
      parent.remove(mesh);
    }
  }

  async disassembleSequential(duration = 600) {
    if (this._disassembling) return;
    if (this.assemblyGroups.length === 0) return;

    this._disassembling = true;
    this._disassembleStage = 0;

    this.resetPositions();
    this._clearPathLines();

    try {
      const stageMap = new Map();
      for (const g of this.assemblyGroups) {
        const s = g.stage || 1;
        if (!stageMap.has(s)) stageMap.set(s, []);
        stageMap.get(s).push(g);
      }
      const stages = Array.from(stageMap.keys()).sort((a, b) => a - b);
      const stageDuration = Math.max(duration / stages.length, 300);

      for (const stage of stages) {
        this._disassembleStage = stage;
        this._setStatus('拆卸阶段 ' + stage + ' / ' + stages.length);

        const groups = stageMap.get(stage);
        for (const group of groups) {
          const dir = this._directionToVector(group.direction);
          const dist = this.explosionDistance * (group.distanceMultiplier || 1);
          const meshesToRemove = group.meshes.filter(m =>
            !this._isFixedMesh(m) && !this._isExplosionCenter(m));
          if (meshesToRemove.length === 0) continue;

          const preFadeOpacities = new Map();
          for (const m of meshesToRemove) {
            preFadeOpacities.set(m, this._getMeshOpacity(m));
          }

          await this._animateRemoval(meshesToRemove, dir, dist, stageDuration / 2);
          await this._fadeOut(meshesToRemove, Math.max(stageDuration / 4, 200));
          this._removeMeshesFromScene(meshesToRemove, preFadeOpacities);
          await new Promise(r => setTimeout(r, 100));
        }
      }

      this._setStatus('拆卸演示完成');
    } catch (e) {
      console.error('disassembleSequential error:', e);
      this._setStatus('拆卸演示出错');
    } finally {
      this._disassembling = false;
    }
  }

  async disassembleOneStep(duration = 600) {
    if (this.assemblyGroups.length === 0) return;

    if (this._disassembling) return;

    const stageMap = new Map();
    for (const g of this.assemblyGroups) {
      const s = g.stage || 1;
      if (!stageMap.has(s)) stageMap.set(s, []);
      stageMap.get(s).push(g);
    }
    const stages = Array.from(stageMap.keys()).sort((a, b) => a - b);
    const stageDuration = Math.max(duration, 400);

    let found = false;
    for (const stage of stages) {
      if (this._disassembleStage > 0 && stage < this._disassembleStage) continue;
      const groups = stageMap.get(stage);
      for (const group of groups) {
        const meshesToRemove = group.meshes.filter(m =>
          !this._isFixedMesh(m) && !this._isExplosionCenter(m));
        if (meshesToRemove.length === 0) continue;
        if (!this._removedMeshes.has(meshesToRemove[0])) {
          const dir = this._directionToVector(group.direction);
          const dist = this.explosionDistance * (group.distanceMultiplier || 1);

          this._disassembling = true;
          this._disassembleStage = stage;
          this._setStatus('单步拆卸: 阶段 ' + stage);

          try {
            const preFadeOpacities = new Map();
            for (const m of meshesToRemove) {
              preFadeOpacities.set(m, this._getMeshOpacity(m));
            }

            await this._animateRemoval(meshesToRemove, dir, dist, stageDuration / 2);
            await this._fadeOut(meshesToRemove, Math.max(stageDuration / 4, 200));
            this._removeMeshesFromScene(meshesToRemove, preFadeOpacities);

            this._setStatus('单步拆卸完成 — 阶段 ' + stage);
          } catch (e) {
            console.error('disassembleOneStep error:', e);
            this._setStatus('单步拆卸出错');
          } finally {
            this._disassembling = false;
          }
          found = true;
          break;
        }
      }
      if (found) break;
    }

    if (!found) {
      this._setStatus('所有阶段已完成拆卸 — 请先复位');
    }
  }

  restoreAll() {
    for (const [mesh, info] of this._removedMeshes) {
      if (info.parent && info.parent.add) {
        info.parent.add(mesh);
      } else {
        this.scene.add(mesh);
      }
      const origLocal = this.originalLocalPositions.get(mesh);
      mesh.position.copy(origLocal || info.localPosition || info.position);
      this.explodedPositions.set(mesh, info.position.clone());
      mesh.visible = true;
      // Prefer snapshot if present
      if (this._matSnapshot.has(mesh)) {
        this._restoreMaterialState(mesh);
      } else if (mesh.material) {
        this._setMeshOpacity(mesh, info.opacity, info.transparent);
        if (info.depthWrite !== undefined) {
          const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
          for (const mat of mats) { mat.depthWrite = info.depthWrite; }
        }
      }
    }
    this._removedMeshes.clear();
    this._clearPathLines();
    this.clearThrustLines();
    for (const [mesh, pos] of this.originalPositions) {
      if (this._isFixedMesh(mesh)) continue;
      if (this._isExplosionCenter(mesh)) continue;
      const origLocal = this.originalLocalPositions.get(mesh);
      if (origLocal) mesh.position.copy(origLocal);
      this.explodedPositions.set(mesh, pos.clone());
    }
    this._disassembling = false;
    this._disassembleStage = 0;
    this.isExploded = false;
    this._setStatus('已复位全部零件');
  }
}
