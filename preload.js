/**
 * Preload script — exposes safe IPC bridge to renderer.
 *
 * Uses contextBridge to expose electronAPI with file dialogs,
 * screenshot saving, and pipeline progress events.
 */

const { contextBridge, ipcRenderer } = require('electron');

const _listenerMap = new Map();

function exclusiveOn(channel, cb) {
  const old = _listenerMap.get(channel);
  if (old) ipcRenderer.removeListener(channel, old);
  const wrapped = (_event, ...args) => cb(...args);
  ipcRenderer.on(channel, wrapped);
  _listenerMap.set(channel, wrapped);
  return () => {
    ipcRenderer.removeListener(channel, wrapped);
    _listenerMap.delete(channel);
  };
}

contextBridge.exposeInMainWorld('electronAPI', {
  // ── Dialog ────────────────────────────────────────────

  /** Open file dialog for assembly.json. Returns { filePath, content, dir } or null. */
  selectAssemblyJson: () => ipcRenderer.invoke('select-assembly-json'),

  /** Save PNG screenshot. Pass dataUrl, returns saved path or false. */
  saveScreenshot: (dataUrl) => ipcRenderer.invoke('save-screenshot', dataUrl),

  /** Save SVG export. Pass svg string, returns saved path or false. */
  saveSVG: (svgString) => ipcRenderer.invoke('save-svg', svgString),

  /** Read file at given path. Returns Buffer. */
  readFile: (filePath) => ipcRenderer.invoke('read-file', filePath),

  /** Check if file exists. Returns bool. */
  fileExists: (filePath) => ipcRenderer.invoke('file-exists', filePath),

  // ── Body shells ────────────────────────────────────────

  /** List user-added car body models. Returns [{ name, glb }]. */
  listUserBodies: () => ipcRenderer.invoke('list-user-bodies'),

  /** Import a new body shell from STP → converts to .glb. */
  importBody: () => ipcRenderer.invoke('import-body'),

  /** Import body from known STP path (no dialog). */
  importBodyFromPath: (stpPath) => ipcRenderer.invoke('import-body-from-path', stpPath),

  /** Run pipeline scoped to a specific sub-assembly node. */
  runPipelineForNode: (rootNode) => ipcRenderer.invoke('run-pipeline-for-node', rootNode),
  /** Run pipeline for node using cached STP path (no file dialog). */
  runPipelineForNodeCached: (stpPath, rootNode, compoundsJson) =>
    ipcRenderer.invoke('run-pipeline-for-node-cached', stpPath, rootNode, compoundsJson),

  /** Run BOM preview pipeline (BOM → mesh + glb, no analysis). */
  runBomPreviewPipeline: () => ipcRenderer.invoke('run-bom-preview-pipeline'),

  /** Run BOM full pipeline with optional target part for dependency chain. */
  runBomFullPipeline: (targetPart) => ipcRenderer.invoke('run-bom-full-pipeline', targetPart),

  /** Run BOM full pipeline with cached paths (no file dialog). */
  runBomFullPipelineCached: (bomPath, modelsDir, targetPart) => ipcRenderer.invoke('run-bom-full-pipeline-cached', bomPath, modelsDir, targetPart),
  /** Run single-file STP dependency chain analysis for a target part. */
  runSinglePipelineChain: (stpPath, targetPart, compoundsJson) =>
    ipcRenderer.invoke('run-single-pipeline-chain', stpPath, targetPart, compoundsJson),

  /** Run preview pipeline (STP → mesh + glb, no analysis). */
  runPreviewPipeline: () => ipcRenderer.invoke('run-preview-pipeline'),

  /** Run geometric explosion pipeline (single STP) with optional center part. */
  runExplosionPipelineWithCenter: (stpPath, centerPart) =>
    ipcRenderer.invoke('run-explosion-pipeline-with-center', stpPath, centerPart),

  /** Run geometric explosion pipeline (BOM mode) with optional center part. */
  runBomExplosionPipelineCached: (bomPath, modelsDir, centerPart) =>
    ipcRenderer.invoke('run-bom-explosion-pipeline-cached', bomPath, modelsDir, centerPart),

  /** Run model cleaning pipeline (STP + XLSX → clean assembly). */
  runCleanPipeline: () => ipcRenderer.invoke('run-clean-pipeline'),

  /** Run model comparison pipeline (XLSX Sheet3 → comparison report). */
  runComparePipeline: () => ipcRenderer.invoke('run-compare-pipeline'),

  // ── Pipeline ──────────────────────────────────────────

  /** Listen for pipeline stdout progress lines. */
  onPipelineProgress: (callback) => exclusiveOn('pipeline-progress', callback),

  /** Pipeline mode: 'preview' or 'full'. Sent before pipeline-started. */
  onPipelineMode: (callback) => exclusiveOn('pipeline-mode', callback),

  /** Pipeline started. */
  onPipelineStarted: (callback) => exclusiveOn('pipeline-started', callback),

  /** Pipeline completed successfully. */
  onPipelineComplete: (callback) => exclusiveOn('pipeline-complete', callback),

  /** Pipeline failed. */
  onPipelineError: (callback) => exclusiveOn('pipeline-error', callback),

  // ── Menu events ───────────────────────────────────────

  /** Menu: File > Load assembly */
  onMenuLoadAssembly: (callback) => exclusiveOn('menu-load-assembly', callback),

  /** Menu: View > Reset camera */
  onMenuResetCamera: (callback) => exclusiveOn('menu-reset-camera', callback),

  /** Menu: View > 左后方 */
  onMenuViewLeftRear: (callback) => exclusiveOn('menu-view-left-rear', callback),

  /** Menu: View > 左前方 */
  onMenuViewLeftFront: (callback) => exclusiveOn('menu-view-left-front', callback),

  /** Menu: View > 右后方 */
  onMenuViewRightRear: (callback) => exclusiveOn('menu-view-right-rear', callback),

  /** Menu: View > 右前方 */
  onMenuViewRightFront: (callback) => exclusiveOn('menu-view-right-front', callback),

  /** Menu: View > 俯视 */
  onMenuViewTop: (callback) => exclusiveOn('menu-view-top', callback),

  /** Menu: View > 仰视 */
  onMenuViewBottom: (callback) => exclusiveOn('menu-view-bottom', callback),

  /** Menu: View > 位置图截取视角 */
  onMenuViewPositionCapture: (callback) => exclusiveOn('menu-view-position-capture', callback),

  /** Menu: Export > Screenshot */
  onMenuScreenshot: (callback) => exclusiveOn('menu-screenshot', callback),

  /** Menu: Export > Batch capture */
  onMenuBatchCapture: (callback) => exclusiveOn('menu-batch-capture', callback),

  /** Menu: Help > About */
  onMenuShowHelp: (callback) => exclusiveOn('menu-show-help', callback),

  /** Batch position capture: open 3 dialogs */
  selectBatchPositionFiles: () => ipcRenderer.invoke('select-batch-position-files'),

  /** Run BOM preview pipeline with cached paths (invoke, returns jsonPath) */
  runBomPreviewPipelineCached: (bomPath, modelsDir) =>
    ipcRenderer.invoke('run-bom-preview-cached', bomPath, modelsDir),

  /** Save PNG to disk without dialog */
  saveBatchPng: (dataUrl, filePath) => {
    const base64 = dataUrl.replace(/^data:image\/png;base64,/, '');
    return ipcRenderer.invoke('save-batch-file', filePath, {
      data: base64,
      encoding: 'base64',
    });
  },

  /** Save SVG string to disk without dialog */
  saveBatchSvg: (svgString, filePath) =>
    ipcRenderer.invoke('save-batch-svg', filePath, svgString),

  /** Get help-images directory path (packaged or dev) */
  getHelpImagesPath: () => ipcRenderer.invoke('get-help-images-path'),

  /** List image files in a help-images subfolder (sorted by numeric name) */
  listHelpImages: (folder) => ipcRenderer.invoke('list-help-images', folder),

  /** Read a help image file as base64 data URL */
  readHelpImage: (folder, filename) => ipcRenderer.invoke('read-help-image', folder, filename),

  // ── Cleanup ───────────────────────────────────────────

  removeAllListeners: (channel) => {
    const old = _listenerMap.get(channel);
    if (old) {
      ipcRenderer.removeListener(channel, old);
      _listenerMap.delete(channel);
    }
    ipcRenderer.removeAllListeners(channel);
  },
});
