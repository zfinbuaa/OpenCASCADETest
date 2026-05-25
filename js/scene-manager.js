/**
 * Scene Manager — Three.js scene setup, lighting, camera, orbit controls,
 * resize handling, background, and standard view presets.
 */

import * as THREE from 'three';
import { OrbitControls } from './three-addons/controls/OrbitControls.js';

export class SceneManager {

  /**
   * @param {HTMLElement} container - DOM element for the renderer.
   * @param {object} options - { backgroundColor, antialias }
   */
  constructor(container, options = {}) {
    this.container = container;

    this.renderer = new THREE.WebGLRenderer({
      antialias: options.antialias !== false,
      preserveDrawingBuffer: true,
      logarithmicDepthBuffer: true,
    });
    this.renderer.setPixelRatio(window.devicePixelRatio);
    this.renderer.setSize(container.clientWidth, container.clientHeight);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.2;
    container.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(options.backgroundColor || 0xffffff);

    this.camera = new THREE.PerspectiveCamera(
      45,
      container.clientWidth / container.clientHeight,
      10,
      500000
    );
    this.camera.position.set(2000, 1500, 2500);
    this.camera.lookAt(0, 500, 0);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.target.set(0, 500, 0);
    this.controls.update();

    this._sceneCenter = new THREE.Vector3(0, 0, 0);
    this._sceneRadius = 1000;

    this._setupLights();

    this._onResize = this._handleResize.bind(this);
    window.addEventListener('resize', this._onResize);

    this._animate = this._animate.bind(this);
    this._animFrame = requestAnimationFrame(this._animate);
  }

  _setupLights() {
    const ambient = new THREE.AmbientLight(0xffffff, 2.0);
    this.scene.add(ambient);

    const key = new THREE.DirectionalLight(0xffffff, 3.0);
    key.position.set(1, 1, 1);
    this.scene.add(key);

    const fill = new THREE.DirectionalLight(0xffffff, 1.5);
    fill.position.set(-1, 0.5, -1);
    this.scene.add(fill);

    const hemi = new THREE.HemisphereLight(0xffffff, 0xe8e8e8, 0.8);
    this.scene.add(hemi);
  }

  _handleResize() {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  _animate() {
    this._animFrame = requestAnimationFrame(this._animate);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  resetCamera() {
    this.camera.position.set(2000, 1500, 2500);
    this.controls.target.set(0, 500, 0);
    this.controls.update();
  }

  focusOn(center, diagonal = 2000) {
    this._sceneCenter.copy(center);
    this._sceneRadius = diagonal / 2;
    this.controls.target.copy(center);
    const fov = this.camera.fov * Math.PI / 180;
    const dist = this._sceneRadius / Math.tan(fov / 2) * 0.9;
    const dir = new THREE.Vector3(1, 0.7, 1).normalize();
    this.camera.position.copy(center).add(dir.multiplyScalar(dist));
    this.controls.update();
  }

  _viewFromDirection(dx, dy, dz) {
    const fov = this.camera.fov * Math.PI / 180;
    const dist = this._sceneRadius / Math.tan(fov / 2) * 0.9;
    const dir = new THREE.Vector3(dx, dy, dz).normalize();
    this.camera.position.copy(this._sceneCenter).add(dir.multiplyScalar(dist));
    this.controls.target.copy(this._sceneCenter);
    this.camera.up.set(0, 1, 0);
    this.controls.update();
  }

  viewFront()  { this._viewFromDirection(0, 0, 1); }
  viewBack()   { this._viewFromDirection(0, 0, -1); }
  viewLeft()   { this._viewFromDirection(-1, 0, 0); }
  viewRight()  { this._viewFromDirection(1, 0, 0); }
  viewTop()    { this._viewFromDirection(0, 1, 0.001); }
  viewBottom() { this._viewFromDirection(0, -1, 0.001); }
  viewIsometric() { this._viewFromDirection(1, 0.7, 1); }

  getSceneCenter() {
    const box = new THREE.Box3();
    this.scene.traverse((child) => {
      if (child.isMesh) {
        box.expandByObject(child);
      }
    });
    if (box.isEmpty()) return new THREE.Vector3(0, 0, 0);
    const center = new THREE.Vector3();
    box.getCenter(center);
    return center;
  }

  getSceneBBox() {
    const box = new THREE.Box3();
    this.scene.traverse((child) => {
      if (child.isMesh) {
        box.expandByObject(child);
      }
    });
    return box;
  }

  dispose() {
    cancelAnimationFrame(this._animFrame);
    window.removeEventListener('resize', this._onResize);
    this.controls.dispose();
    this.renderer.dispose();
    this.container.removeChild(this.renderer.domElement);
  }
}
