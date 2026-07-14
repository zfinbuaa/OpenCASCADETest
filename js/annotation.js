/**
 * Annotation — 白底黑字圆圈标注 + 水平直线引线。
 *
 * 左右双列布局：标签数量左右均衡（奇数时左侧多一个），
 * 引线为水平直线，末端短垂直线指向零件。
 * 每个被勾选的节点视为一个整体，生成一个标注。
 */
import * as THREE from '../node_modules/three/build/three.module.js';

const CIRCLE_R = 12;
const COLUMN_MARGIN = 60;
const LINE_COLOR = '#222222';

export class Annotation {
  constructor(scene, camera, viewportContainer) {
    this.scene = scene;
    this.camera = camera;
    this.container = viewportContainer;
    this.annotations = null;
    this.visible = false;

    this._canvas = document.createElement('canvas');
    this._canvas.style.position = 'absolute';
    this._canvas.style.top = '0';
    this._canvas.style.left = '0';
    this._canvas.style.pointerEvents = 'none';
    this._canvas.style.zIndex = '5';
    this._ctx = this._canvas.getContext('2d');
    this._hiddenPartIds = new Set();
  }

  setHiddenPartIds(ids) {
    this._hiddenPartIds = ids instanceof Set ? ids : new Set(ids || []);
  }

  setParts(parts, checkedNodes = null, compounds = null) {
    if (compounds && compounds.length > 0) {
      this.annotations = compounds.map((c, i) => ({
        partId: c.name,
        partIds: new Set(c.members),
        partName: c.name,
        worldPos: new THREE.Vector3(),
        index: i,
      }));
      return;
    }
    this.annotations = [];
  }

  setSingleLabel(partIds, labelText) {
    this.annotations = [{
      partIds: new Set(partIds),
      partName: labelText,
      index: 0,
      worldPos: new THREE.Vector3(),
    }];
  }

  setPmiLabels(labels) {
    if (!labels || labels.length === 0) { this.annotations = []; return; }
    this.annotations = labels.map((item, i) => ({
      partId: item.partId,
      partName: item.label,
      worldPos: item.targetWorldPos instanceof THREE.Vector3
        ? item.targetWorldPos
        : new THREE.Vector3(
            (item.targetWorldPos && item.targetWorldPos[0]) || 0,
            (item.targetWorldPos && item.targetWorldPos[1]) || 0,
            (item.targetWorldPos && item.targetWorldPos[2]) || 0),
      index: i,
      labelText: item.label,
      partIds: new Set([item.partId]),
    }));
  }

  updatePositions() {
    if (!this.annotations) return;
    for (const ann of this.annotations) {
      const partIds = ann.partIds || new Set([ann.partId]);
      let anyVisible = false;
      const box = new THREE.Box3();
      this.scene.traverse((child) => {
        if (child.isMesh && partIds.has(child.userData.partId)) {
          if (this._hiddenPartIds.has(child.userData.partId)) return;
          if (!child.visible) return;
          box.expandByObject(child);
          anyVisible = true;
        }
      });
      if (anyVisible && !box.isEmpty()) {
        box.getCenter(ann.worldPos);
      }
    }
  }

  getScreenData() {
    if (!this.annotations) return [];
    this.updatePositions();
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    const halfW = w / 2;
    const halfH = h / 2;
    const screenPoints = [];
    for (const ann of this.annotations) {
      const sp = ann.worldPos.clone().project(this.camera);
      if (sp.z > 1) continue;
      const sx = (sp.x * halfW) + halfW;
      const sy = -(sp.y * halfH) + halfH;
      if (sx < -100 || sx > w + 100 || sy < -100 || sy > h + 100) continue;
      screenPoints.push({ sx, sy, ann });
    }
    if (screenPoints.length === 0) return [];
    screenPoints.sort((a, b) => a.sy - b.sy);
    const n = screenPoints.length;
    const leftCount = Math.ceil(n / 2);
    const rightCount = Math.floor(n / 2);
    const leftX = COLUMN_MARGIN;
    const rightX = w - COLUMN_MARGIN;
    const topPad = 40;
    const availH = h - 80;
    const result = [];
    let prevLeftSY = -999;
    for (let i = 0; i < leftCount; i++) {
      const { sx, sy, ann } = screenPoints[i];
      let cy = topPad + (availH / (leftCount + 1)) * (i + 1);
      if (i > 0 && Math.abs(sy - prevLeftSY) < 40) cy += 25;
      prevLeftSY = sy;
      result.push({
        circleX: leftX, circleY: cy,
        targetX: sx, targetY: sy,
        number: ann.index + 1,
        partId: ann.partId, partName: ann.partName,
        labelText: ann.labelText,
      });
    }
    let prevRightSY = -999;
    for (let i = 0; i < rightCount; i++) {
      const { sx, sy, ann } = screenPoints[leftCount + i];
      let cy = topPad + (availH / (rightCount + 1)) * (i + 1);
      if (i > 0 && Math.abs(sy - prevRightSY) < 40) cy += 25;
      prevRightSY = sy;
      result.push({
        circleX: rightX, circleY: cy,
        targetX: sx, targetY: sy,
        number: ann.index + 1,
        partId: ann.partId, partName: ann.partName,
        labelText: ann.labelText,
      });
    }
    return result;
  }

  draw() {
    if (!this.visible || !this.annotations) {
      this._ctx.clearRect(0, 0, this._canvas.width, this._canvas.height);
      return;
    }

    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    this._canvas.width = w;
    this._canvas.height = h;
    const ctx = this._ctx;
    ctx.clearRect(0, 0, w, h);

    this.updatePositions();

    const halfW = w / 2;
    const halfH = h / 2;
    const screenPoints = [];

    for (const ann of this.annotations) {
      const sp = ann.worldPos.clone().project(this.camera);
      if (sp.z > 1) continue;
      const sx = (sp.x * halfW) + halfW;
      const sy = -(sp.y * halfH) + halfH;
      if (sx < -100 || sx > w + 100 || sy < -100 || sy > h + 100) continue;
      screenPoints.push({ sx, sy, ann });
    }

    if (screenPoints.length === 0) return;

    screenPoints.sort((a, b) => a.sy - b.sy);

    const n = screenPoints.length;
    const leftCount = Math.ceil(n / 2);
    const rightCount = Math.floor(n / 2);

    const leftX = COLUMN_MARGIN;
    const rightX = w - COLUMN_MARGIN;

    const topPad = 40;
    const botPad = 40;
    const availH = h - topPad - botPad;

    function drawOne(ctx, circleX, targetX, targetY, cy, number, labelText) {
      const dir = targetX > circleX ? 1 : -1;

      if (labelText) {
        // PMI text label: capsule shape with adaptive width
        ctx.font = 'bold 10px -apple-system, "Microsoft YaHei", sans-serif';
        const textW = ctx.measureText(labelText).width;
        const capH = 24;
        const capR = capH / 2;
        const padX = 8;
        const capW = Math.max(capH, textW + padX * 2);
        const capLeft = circleX - capW / 2;
        const capRight = circleX + capW / 2;

        const horStartX = dir > 0 ? capRight + 1 : capLeft - 1;
        const horEndX = targetX - dir * 6;

        ctx.beginPath();
        ctx.strokeStyle = LINE_COLOR;
        ctx.lineWidth = 1.5;
        ctx.moveTo(horStartX, cy);
        ctx.lineTo(horEndX, cy);
        if (Math.abs(targetY - cy) > 2) {
          ctx.lineTo(horEndX, targetY);
        }
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(capLeft + capR, cy - capR);
        ctx.lineTo(capRight - capR, cy - capR);
        ctx.arc(capRight - capR, cy, capR, -Math.PI / 2, Math.PI / 2);
        ctx.lineTo(capLeft + capR, cy + capR);
        ctx.arc(capLeft + capR, cy, capR, Math.PI / 2, -Math.PI / 2);
        ctx.closePath();
        ctx.fillStyle = '#ffffff';
        ctx.fill();
        ctx.strokeStyle = '#000000';
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.fillStyle = '#000000';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(labelText, circleX, cy);

      } else {
        // Number label: circle (unchanged)
        const horStartX = circleX + dir * (CIRCLE_R + 1);
        const horEndX = targetX - dir * 6;

        ctx.beginPath();
        ctx.strokeStyle = LINE_COLOR;
        ctx.lineWidth = 1.5;
        ctx.moveTo(horStartX, cy);
        ctx.lineTo(horEndX, cy);
        if (Math.abs(targetY - cy) > 2) {
          ctx.lineTo(horEndX, targetY);
        }
        ctx.stroke();

        ctx.beginPath();
        ctx.arc(circleX, cy, CIRCLE_R + 2, 0, Math.PI * 2);
        ctx.fillStyle = '#ffffff';
        ctx.fill();
        ctx.strokeStyle = '#000000';
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.fillStyle = '#000000';
        ctx.font = 'bold 12px -apple-system, "Microsoft YaHei", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(String(number), circleX, cy);
      }
    }

    let prevLeftSY = -999;
    for (let i = 0; i < leftCount; i++) {
      const { sx, sy, ann } = screenPoints[i];
      let cy = topPad + (availH / (leftCount + 1)) * (i + 1);
      if (i > 0 && Math.abs(sy - prevLeftSY) < 40) { cy += 25; }
      prevLeftSY = sy;
      drawOne(ctx, leftX, sx, sy, cy, ann.index + 1, ann.labelText);
    }

    let prevRightSY = -999;
    for (let i = 0; i < rightCount; i++) {
      const { sx, sy, ann } = screenPoints[leftCount + i];
      let cy = topPad + (availH / (rightCount + 1)) * (i + 1);
      if (i > 0 && Math.abs(sy - prevRightSY) < 40) { cy += 25; }
      prevRightSY = sy;
      drawOne(ctx, rightX, sx, sy, cy, ann.index + 1, ann.labelText);
    }
  }

  _findMesh(partId) {
    let found = null;
    this.scene.traverse((child) => {
      if (child.isMesh && child.userData.partId === partId) {
        found = child;
      }
    });
    return found;
  }

  composeToCanvas(rendererCanvas) {
    const w = rendererCanvas.width;
    const h = rendererCanvas.height;
    const composed = document.createElement('canvas');
    composed.width = w;
    composed.height = h;
    const ctx = composed.getContext('2d');
    ctx.drawImage(rendererCanvas, 0, 0);
    this.draw();
    if (this._canvas.width > 0) {
      ctx.drawImage(this._canvas, 0, 0, this._canvas.width, this._canvas.height, 0, 0, w, h);
    }
    return composed;
  }

  show() {
    this.visible = true;
    if (!this._canvas.parentNode) {
      this.container.appendChild(this._canvas);
    }
  }

  hide() {
    this.visible = false;
    if (this._canvas.parentNode) {
      this.container.removeChild(this._canvas);
    }
  }

  toggle() {
    if (this.visible) this.hide(); else this.show();
  }

  clear() {
    this.hide();
    this.annotations = null;
  }
}
