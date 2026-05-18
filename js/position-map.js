/**
 * Position Map — transparent body shell with highlighted part overlay.
 *
 * Renders a ghost/transparent version of the full assembly while
 * highlighting selected parts with full opacity.
 */

import * as THREE from 'three';

export class PositionMap {

  /**
   * @param {THREE.Scene} scene
   */
  constructor(scene) {
    this.scene = scene;
    this.ghostMeshes = [];    // meshes in transparent mode
    this.highlighted = new Map(); // mesh -> original material
  }

  /**
   * Create a ghost map from all loaded meshes.
   * Makes all meshes semi-transparent so the assembly outline is visible.
   */
  createGhostMap(meshes) {
    this.clearGhost();
    for (const mesh of meshes) {
      this._makeGhost(mesh);
    }
  }

  /**
   * Make a single mesh ghost-like (semi-transparent wireframe overlay).
   */
  _makeGhost(mesh) {
    if (!mesh.isMesh) return;

    // Store original materials
    mesh.userData._originalMaterials = mesh.userData._originalMaterials || [];

    if (Array.isArray(mesh.material)) {
      mesh.userData._originalMaterials = mesh.material.slice();
    } else {
      mesh.userData._originalMaterials = [mesh.material];
    }

    const ghostMat = new THREE.MeshPhongMaterial({
      color: 0x4488cc,
      transparent: true,
      opacity: 0.2,
      side: THREE.DoubleSide,
      depthWrite: false,
    });

    mesh.material = ghostMat;
    this.ghostMeshes.push(mesh);

    // Add wireframe overlay
    const wireframe = new THREE.LineSegments(
      new THREE.EdgesGeometry(mesh.geometry),
      new THREE.LineBasicMaterial({
        color: 0x4488cc,
        transparent: true,
        opacity: 0.15,
        depthTest: true,
      })
    );
    wireframe.position.copy(mesh.position);
    wireframe.rotation.copy(mesh.rotation);
    wireframe.scale.copy(mesh.scale);
    this.scene.add(wireframe);
    mesh.userData._wireframe = wireframe;
  }

  /**
   * Highlight specific meshes (restore to original material, full opacity).
   */
  highlight(meshes) {
    for (const mesh of meshes) {
      if (!mesh.isMesh) continue;

      // Remove ghost wireframe
      if (mesh.userData._wireframe) {
        this.scene.remove(mesh.userData._wireframe);
        mesh.userData._wireframe = null;
      }

      // Restore original material
      const originals = mesh.userData._originalMaterials;
      if (originals && originals.length > 0) {
        mesh.material = originals.length === 1 ? originals[0] : originals;
      }

      // Remove from ghost list
      const idx = this.ghostMeshes.indexOf(mesh);
      if (idx >= 0) this.ghostMeshes.splice(idx, 1);
    }
  }

  /**
   * Un-ghost all meshes, restoring original materials.
   */
  clearGhost() {
    for (const mesh of this.ghostMeshes) {
      if (!mesh.isMesh) continue;

      if (mesh.userData._wireframe) {
        this.scene.remove(mesh.userData._wireframe);
        mesh.userData._wireframe = null;
      }

      const originals = mesh.userData._originalMaterials;
      if (originals && originals.length > 0) {
        mesh.material = originals.length === 1 ? originals[0] : originals;
      }
    }
    this.ghostMeshes = [];
  }

  /**
   * Set ghost opacity for all ghosted meshes.
   */
  setGhostOpacity(opacity) {
    for (const mesh of this.ghostMeshes) {
      if (mesh.material && mesh.material.opacity !== undefined) {
        mesh.material.opacity = Math.max(0.02, Math.min(1, opacity));
        mesh.material.transparent = opacity < 1;
      }
    }
  }
}
