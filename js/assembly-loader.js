/**
 * Assembly Loader — loads assembly.json and all referenced glb files.
 *
 * Consumes the output of the OCCT pipeline and produces data structures
 * ready for the explosion-view engine.
 *
 * Usage:
 *   const { assembly, groups } = await AssemblyLoader.loadAssembly('path/to/assembly.json');
 */

export class AssemblyLoader {

  /**
   * Load an assembly.json file and all referenced glb models.
   *
   * @param {string} jsonPath - URL or path to assembly.json
   * @param {object} loader - Three.js GLTFLoader instance
   * @returns {Promise<{ assembly: object, loadedParts: Map, groups: Array }>}
   */
  static async loadAssembly(jsonPath, loader) {
    // Determine base directory
    const baseDir = jsonPath.substring(0, jsonPath.lastIndexOf('/') + 1);

    // 1. Read assembly.json
    const response = await fetch(jsonPath);
    const assembly = await response.json();

    // 2. Batch load all glb files
    const loadedParts = new Map();
    const loadPromises = assembly.parts.map(async (part) => {
      const glbUrl = baseDir + part.glbFile;
      const gltfData = await this._loadGLB(glbUrl, loader);
      loadedParts.set(part.id, {
        ...part,
        gltfData,
        meshes: this._collectMeshes(gltfData.scene),
      });
    });
    await Promise.all(loadPromises);

    // 3. Convert to assembly groups (direct match with explosion-view.js)
    const groups = this._buildGroups(assembly, loadedParts);

    return { assembly, loadedParts, groups };
  }

  /**
   * Build AssemblyGroup[] from assembly data.
   * Groups are defined in assembly.json; solo parts become solo groups.
   */
  static _buildGroups(assembly, loadedParts) {
    const groups = [];

    // Explicit groups from JSON
    if (assembly.groups) {
      for (const group of assembly.groups) {
        const meshes = [];
        const firstPart = loadedParts.get(group.members[0]);
        const firstDirection = firstPart
          ? firstPart.direction || '+Y'
          : '+Y';

        for (const memberId of group.members) {
          const part = loadedParts.get(memberId);
          if (part && part.meshes) {
            meshes.push(...part.meshes);
          }
        }

        groups.push({
          id: group.id,
          name: group.name,
          meshes: meshes,
          direction: firstDirection,
          distanceMultiplier: 1.0,
          stage: group.stage,
        });
      }
    }

    // Solo groups: parts not in any explicit group
    const groupedIds = new Set();
    if (assembly.groups) {
      for (const g of assembly.groups) {
        for (const m of g.members) {
          groupedIds.add(m);
        }
      }
    }

    for (const part of assembly.parts) {
      if (!groupedIds.has(part.id)) {
        const data = loadedParts.get(part.id);
        if (data && data.meshes) {
          groups.push({
            id: part.id,
            name: part.name,
            meshes: data.meshes,
            direction: part.direction || '+Y',
            distanceMultiplier: part.distanceMultiplier || 1.0,
            stage: part.disassemblyStage || 1,
          });
        }
      }
    }

    return groups;
  }

  /**
   * Load a single .glb file via GLTFLoader.
   */
  static async _loadGLB(url, loader) {
    return new Promise((resolve, reject) => {
      loader.load(
        url,
        (gltf) => resolve(gltf),
        undefined,
        (err) => reject(err)
      );
    });
  }

  /**
   * Recursively collect all Mesh objects from a Three.js Object3D tree.
   */
  static _collectMeshes(root) {
    const meshes = [];
    root.traverse((child) => {
      if (child.isMesh) {
        meshes.push(child);
      }
    });
    return meshes;
  }
}
