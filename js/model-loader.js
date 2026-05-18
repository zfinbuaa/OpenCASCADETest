/**
 * Model Loader — GLTF/GLB file loading for Three.js.
 *
 * Provides a simple interface over GLTFLoader for loading models
 * and extracting meshes, with progress reporting.
 */

import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

export class ModelLoader {

  constructor() {
    this._loader = new GLTFLoader();
  }

  /**
   * Load a single .glb file via URL.
   * Works with local:// URLs (Electron custom scheme) or https:// (CDN).
   */
  async loadModel(url, onProgress = null) {
    return new Promise((resolve, reject) => {
      this._loader.load(
        url,
        (gltf) => {
          resolve({
            scene: gltf.scene,
            meshes: this._collectMeshes(gltf.scene),
            animations: gltf.animations || [],
          });
        },
        (xhr) => {
          if (onProgress && xhr.total > 0) {
            onProgress(xhr.loaded / xhr.total);
          }
        },
        (err) => reject(err)
      );
    });
  }

  /**
   * Recursively collect all Mesh objects from a Three.js scene graph.
   */
  _collectMeshes(root) {
    const meshes = [];
    root.traverse((child) => {
      if (child.isMesh) {
        meshes.push(child);
      }
    });
    return meshes;
  }

  /**
   * Get all meshes from a loaded model root.
   * (static helper for compatibility)
   */
  static getAllMeshes(root) {
    const meshes = [];
    root.traverse((child) => {
      if (child.isMesh) meshes.push(child);
    });
    return meshes;
  }
}
