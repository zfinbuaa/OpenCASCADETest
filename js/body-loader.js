/**
 * Body Loader — 加载内置车壳 .glb 模型并控制透明度。
 *
 * 读取 bodies/manifest.json，提供下拉选择切换车壳。
 */

export class BodyLoader {

  constructor() {
    this.bodies = [];
    this.currentBody = null;   // { name, group: THREE.Group }
  }

  /**
   * 从 manifest.json 加载车壳列表。
   * @param {string} manifestUrl - bodies/manifest.json 路径
   * @param {object} modelLoader - ModelLoader 实例
   */
  async loadManifest(manifestUrl, modelLoader) {
    const resp = await fetch(manifestUrl);
    const data = await resp.json();
    this.bodies = data.bodies || [];
    this._modelLoader = modelLoader;

    await this._appendUserBodies();
  }

  async _appendUserBodies() {
    if (window.electronAPI) {
      try {
        const userBodies = await window.electronAPI.listUserBodies();
        for (const b of userBodies) this.bodies.push(b);
      } catch (e) {
        console.error('Failed to list user bodies:', e);
      }
    }
  }

  async reloadBodies() {
    this.bodies = [];
    try {
      const resp = await fetch('bodies/manifest.json');
      const data = await resp.json();
      this.bodies = data.bodies || [];
    } catch (e) {
      console.error('Failed to reload body manifest:', e);
    }
    await this._appendUserBodies();
  }

  /**
   * 获取车壳名称列表（用于下拉菜单）。
   */
  getBodyNames() {
    return this.bodies.map(b => b.name);
  }

  /**
   * 切换车壳。
   * @param {number} index - bodies 数组索引
   * @param {THREE.Scene} scene
   * @returns {Promise<THREE.Group|null>} 车壳 Group 或 null
   */
  async switchBody(index, scene) {
    // 移除旧车壳
    if (this.currentBody) {
      scene.remove(this.currentBody.group);
      this.currentBody = null;
    }

    if (index < 0 || index >= this.bodies.length) return null;

    const entry = this.bodies[index];
    if (!entry.glb) return null;

    const data = await this._modelLoader.loadModel(entry.glb);
    const group = data.scene;

    // 设置透明度 0.7
    group.traverse((child) => {
      if (child.isMesh && child.material) {
        const materials = Array.isArray(child.material)
          ? child.material
          : [child.material];
        for (const mat of materials) {
          mat.transparent = true;
          mat.opacity = 0.7;
          mat.depthWrite = false;
          mat.needsUpdate = true;
        }
      }
    });

    scene.add(group);
    this.currentBody = { name: entry.name, group };
    return group;
  }

  /**
   * 清除当前车壳。
   */
  clearBody(scene) {
    if (this.currentBody) {
      scene.remove(this.currentBody.group);
      this.currentBody = null;
    }
  }

  /** 当前车壳名称 */
  get currentName() {
    return this.currentBody ? this.currentBody.name : null;
  }
}
