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

  setParts(parts, checkedNodes = null) {
    if (checkedNodes && checkedNodes.length > 0) {
      this.annotations = checkedNodes.map((node, i) => ({
        partId: node.nodeId,
        partIds: node.partIds,
        partName: node.nodeId,
        worldPos: new THREE.Vector3(),
        index: i,
      }));
    } else {
      this.annotations = parts.map((p, i) => ({
        partId: p.id || p.name,
        partIds: new Set([p.id || p.name]),
        partName: p.name || p.id,
        worldPos: new THREE.Vector3(),
        index: i,
      }));
    }
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

    function drawOne(ctx, circleX, targetX, targetY, cy, number) {
      const dir = targetX > circleX ? 1 : -1;
      const horStartX = circleX + dir * (CIRCLE_R + 1);

      ctx.beginPath();
      ctx.strokeStyle = LINE_COLOR;
      ctx.lineWidth = 1.5;

      const horEndX = targetX - dir * 6;
      ctx.moveTo(horStartX, cy);
      ctx.lineTo(horEndX, cy);

      if (Math.abs(targetY - cy) > 2) {
        ctx.lineTo(horEndX, targetY);
      }

      ctx.stroke();

      // White circle
      ctx.beginPath();
      ctx.arc(circleX, cy, CIRCLE_R + 2, 0, Math.PI * 2);
      ctx.fillStyle = '#ffffff';
      ctx.fill();
      ctx.strokeStyle = '#000000';
      ctx.lineWidth = 2;
      ctx.stroke();

      // Number
      ctx.fillStyle = '#000000';
      ctx.font = 'bold 12px -apple-system, "Microsoft YaHei", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(String(number), circleX, cy);
    }

    for (let i = 0; i < leftCount; i++) {
      const { sx, sy, ann } = screenPoints[i];
      const cy = topPad + (availH / (leftCount + 1)) * (i + 1);
      const number = ann.index + 1;
      drawOne(ctx, leftX, sx, sy, cy, number);
    }

    for (let i = 0; i < rightCount; i++) {
      const { sx, sy, ann } = screenPoints[leftCount + i];
      const cy = topPad + (availH / (rightCount + 1)) * (i + 1);
      const number = ann.index + 1;
      drawOne(ctx, rightX, sx, sy, cy, number);
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
