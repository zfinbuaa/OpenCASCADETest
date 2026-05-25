/**
 * Preload script — exposes safe IPC bridge to renderer.
 *
 * Uses contextBridge to expose electronAPI with file dialogs,
 * screenshot saving, and pipeline progress events.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // ── Dialog ────────────────────────────────────────────

  /** Open file dialog for assembly.json. Returns { filePath, content, dir } or null. */
  selectAssemblyJson: () => ipcRenderer.invoke('select-assembly-json'),

  /** Save PNG screenshot. Pass dataUrl, returns saved path or false. */
  saveScreenshot: (dataUrl) => ipcRenderer.invoke('save-screenshot', dataUrl),

  /** Read file at given path. Returns Buffer. */
  readFile: (filePath) => ipcRenderer.invoke('read-file', filePath),

  /** Check if file exists. Returns bool. */
  fileExists: (filePath) => ipcRenderer.invoke('file-exists', filePath),

  // ── Body shells ────────────────────────────────────────

  /** List user-added car body models. Returns [{ name, glb }]. */
  listUserBodies: () => ipcRenderer.invoke('list-user-bodies'),

  /** Import a new body shell from STP → converts to .glb. */
  importBody: () => ipcRenderer.invoke('import-body'),

  /** Run pipeline scoped to a specific sub-assembly node. */
  runPipelineForNode: (rootNode) => ipcRenderer.invoke('run-pipeline-for-node', rootNode),

  // ── Pipeline ──────────────────────────────────────────

  /** Listen for pipeline stdout progress lines. */
  onPipelineProgress: (callback) => {
    ipcRenderer.on('pipeline-progress', (_event, msg) => callback(msg));
  },

  /** Pipeline mode: 'preview' or 'full'. Sent before pipeline-started. */
  onPipelineMode: (callback) => {
    ipcRenderer.on('pipeline-mode', (_event, mode) => callback(mode));
  },

  /** Pipeline started. */
  onPipelineStarted: (callback) => {
    ipcRenderer.on('pipeline-started', (_event, path) => callback(path));
  },

  /** Pipeline completed successfully. */
  onPipelineComplete: (callback) => {
    ipcRenderer.on('pipeline-complete', (_event, jsonPath) => callback(jsonPath));
  },

  /** Pipeline failed. */
  onPipelineError: (callback) => {
    ipcRenderer.on('pipeline-error', (_event, code) => callback(code));
  },

  // ── Menu events ───────────────────────────────────────

  /** Menu: File > Load assembly */
  onMenuLoadAssembly: (callback) => {
    ipcRenderer.on('menu-load-assembly', () => callback());
  },

  /** Menu: View > Reset camera */
  onMenuResetCamera: (callback) => {
    ipcRenderer.on('menu-reset-camera', () => callback());
  },

  /** Menu: View > Toggle ghost mode */
  onMenuToggleGhost: (callback) => {
    ipcRenderer.on('menu-toggle-ghost', () => callback());
  },

  /** Menu: View > Toggle annotations */
  onMenuToggleAnnotations: (callback) => {
    ipcRenderer.on('menu-toggle-annotations', () => callback());
  },

  /** Menu: Export > Screenshot */
  onMenuScreenshot: (callback) => {
    ipcRenderer.on('menu-screenshot', () => callback());
  },

  /** Menu: Export > Batch capture */
  onMenuBatchCapture: (callback) => {
    ipcRenderer.on('menu-batch-capture', () => callback());
  },

  // ── Cleanup ───────────────────────────────────────────

  removeAllListeners: (channel) => {
    ipcRenderer.removeAllListeners(channel);
  },
});
