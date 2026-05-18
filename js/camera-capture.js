/**
 * Camera Capture — automated screenshot capture for each disassembly stage.
 *
 * Renders the scene from multiple preset viewpoints for documentation.
 */

import * as THREE from 'three';

export class CameraCapture {

  /**
   * @param {THREE.WebGLRenderer} renderer
   * @param {THREE.PerspectiveCamera} camera
   * @param {object} controls - OrbitControls instance (optional)
   */
  constructor(renderer, camera, controls = null) {
    this.renderer = renderer;
    this.camera = camera;
    this.controls = controls;
  }

  /**
   * Capture screenshots for all disassembly stages.
   *
   * @param {number} numStages - Total number of disassembly stages
   * @param {THREE.Vector3} focusCenter - Point to orbit around
   * @returns {Promise<Array>} Array of { stage, label, dataUrl }
   */
  async captureAllStages(numStages, focusCenter = new THREE.Vector3(0, 0, 0)) {
    const captures = [];

    for (let stage = 1; stage <= numStages; stage++) {
      const views = [
        this._viewIsometric(stage, focusCenter),
        this._viewDisassemblyDirection(stage, focusCenter),
        this._viewTopDown(stage, focusCenter),
        this._viewFront(stage, focusCenter),
      ];

      for (const { position, target, label } of views) {
        this.camera.position.copy(position);
        this.camera.lookAt(target);

        if (this.controls) {
          this.controls.target.copy(target);
          this.controls.update();
        }

        // Wait for render
        await new Promise((r) => requestAnimationFrame(r));

        const dataUrl = this.renderer.domElement.toDataURL('image/png');
        captures.push({ stage, label, dataUrl });
      }
    }

    return captures;
  }

  /**
   * Single-view capture of the current scene.
   *
   * @returns {string} PNG data URL
   */
  captureCurrent() {
    return this.renderer.domElement.toDataURL('image/png');
  }

  // ---- Viewpoint Presets ----

  _viewIsometric(stage, center) {
    const d = 8;
    return {
      position: new THREE.Vector3(
        center.x + d,
        center.y + d * 0.7,
        center.z + d
      ),
      target: center.clone(),
      label: 'stage_' + stage + '_iso',
    };
  }

  _viewDisassemblyDirection(stage, center) {
    return {
      position: new THREE.Vector3(center.x + 10, center.y + 3, center.z),
      target: center.clone(),
      label: 'stage_' + stage + '_side',
    };
  }

  _viewTopDown(stage, center) {
    return {
      position: new THREE.Vector3(center.x, center.y + 12, center.z + 0.01),
      target: center.clone(),
      label: 'stage_' + stage + '_top',
    };
  }

  _viewFront(stage, center) {
    return {
      position: new THREE.Vector3(center.x, center.y + 3, center.z + 10),
      target: center.clone(),
      label: 'stage_' + stage + '_front',
    };
  }

  /**
   * Capture a custom viewpoint.
   */
  async captureView(position, target, label = 'custom') {
    this.camera.position.copy(position);
    this.camera.lookAt(target);
    if (this.controls) {
      this.controls.target.copy(target);
      this.controls.update();
    }
    await new Promise((r) => requestAnimationFrame(r));
    return {
      label,
      dataUrl: this.renderer.domElement.toDataURL('image/png'),
    };
  }
}
