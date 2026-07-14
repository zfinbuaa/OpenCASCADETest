/**
 * Electron Main Process — 窗口管理、菜单、IPC、Python 管线子进程
 */

const { app, BrowserWindow, Menu, dialog, ipcMain, protocol, net } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

let mainWindow = null;
let _userBodiesDir = null;

const _allowedRoots = new Set();
const _ALLOWED_EXTENSIONS = ['.json', '.glb', '.gltf', '.png', '.jpg', '.jpeg'];
const _SAFE_NAME_RE = /^[\w\-.\u4e00-\u9fa5 ]{1,200}$/;

function registerAllowedRoot(p) {
  if (!p) return;
  _allowedRoots.add(path.resolve(p));
}

function isPathAllowed(target) {
  try {
    const abs = path.resolve(target);
    for (const root of _allowedRoots) {
      const rel = path.relative(root, abs);
      if ((rel && !rel.startsWith('..') && !path.isAbsolute(rel)) || abs === root) return true;
    }
  } catch {}
  return false;
}

function validatePartName(name) {
  if (typeof name !== 'string' || !_SAFE_NAME_RE.test(name) || name.startsWith('--')) {
    return false;
  }
  return true;
}

function safeSend(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

function getUserBodiesDir() {
  if (!_userBodiesDir) {
    _userBodiesDir = path.join(app.getPath('userData'), 'bodies');
  }
  return _userBodiesDir;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1680,
    height: 980,
    minWidth: 1024,
    minHeight: 600,
    backgroundColor: '#ffffff',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
    },
    title: '数模自动拆装工具',
  });

  mainWindow.loadFile('index.html');

  // 开发模式打开 DevTools
  if (process.argv.includes('--dev')) {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function buildMenu() {
  const template = [
    {
      label: '文件',
      submenu: [
        {
          label: '加载单个 STEP 文件',
          accelerator: 'CmdOrCtrl+O',
          click: () => runPreviewPipeline(),
        },
        {
          label: '通过表格加载多个 STEP 文件',
          accelerator: 'CmdOrCtrl+B',
          click: () => runBomPreviewPipeline(),
        },
        {
          label: '数模清洗 (STP + BOM)',
          click: () => runCleanPipeline(),
        },
        {
          label: '数模对比 (对比表格)',
          click: () => runComparePipeline(),
        },
        {
          label: '加载已处理的 JSON 文件',
          click: () => safeSend('menu-load-assembly'),
        },
        { type: 'separator' },
        {
          label: '退出',
          accelerator: 'CmdOrCtrl+Q',
          click: () => app.quit(),
        },
      ],
    },
    {
      label: '管线',
      submenu: [
        {
          label: '生成爆炸路径',
          accelerator: 'CmdOrCtrl+I',
          click: () => runExplosionPipeline(),
        },
        {
          label: '生成拆装方案',
          accelerator: 'CmdOrCtrl+G',
          click: () => runImportPipeline(),
        },
      ],
    },
    {
      label: '视图',
      submenu: [
        {
          label: '复位视角 (右前方)',
          accelerator: 'F',
          click: () => safeSend('menu-reset-camera'),
        },
        { type: 'separator' },
        {
          label: '左后方',
          click: () => safeSend('menu-view-left-rear'),
        },
        {
          label: '左前方',
          click: () => safeSend('menu-view-left-front'),
        },
        {
          label: '右后方',
          click: () => safeSend('menu-view-right-rear'),
        },
        {
          label: '右前方',
          click: () => safeSend('menu-view-right-front'),
        },
        { type: 'separator' },
        {
          label: '俯视',
          click: () => safeSend('menu-view-top'),
        },
        {
          label: '仰视',
          click: () => safeSend('menu-view-bottom'),
        },
        { type: 'separator' },
        {
          label: '位置图截取视角',
          accelerator: 'CmdOrCtrl+P',
          click: () => safeSend('menu-view-position-capture'),
        },
      ],
    },
    {
      label: '导出',
      submenu: [
        {
          label: '截图当前视图',
          accelerator: 'CmdOrCtrl+S',
          click: () => safeSend('menu-screenshot'),
        },
        {
          label: '逐阶段批量截图',
          click: () => safeSend('menu-batch-capture'),
        },
      ],
    },
    {
      label: '帮助',
      submenu: [
        {
          label: '关于',
          click: () => safeSend('menu-show-help'),
        },
      ],
    },
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

// ── Help Images Path ──────────────────────────────────────
function getHelpImagesPath() {
  const fs = require('fs');
  const path = require('path');

  const packagedPath = path.join(process.resourcesPath, 'help-images');
  if (fs.existsSync(packagedPath))
    return packagedPath;

  const devPath = path.join(__dirname, 'help-images');
  if (fs.existsSync(devPath))
    return devPath;

  return null;
}

// ── IPC Handlers ──────────────────────────────────────────

ipcMain.handle('select-assembly-json', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择装配数据文件',
    filters: [{ name: 'Assembly JSON', extensions: ['json'] }],
    properties: ['openFile'],
  });
  if (result.canceled || !result.filePaths[0]) return null;

  const filePath = result.filePaths[0];
  const content = fs.readFileSync(filePath, 'utf-8');
  const dir = path.dirname(filePath);
  // Register the dir as allowed so subsequent file reads in this dir succeed.
  registerAllowedRoot(dir);
  return { filePath, content, dir };
});

ipcMain.handle('save-screenshot', async (_event, dataUrl) => {
  if (typeof dataUrl !== 'string' || !dataUrl.startsWith('data:image/png;base64,')) {
    return false;
  }
  const result = await dialog.showSaveDialog(mainWindow, {
    title: '保存截图',
    defaultPath: 'screenshot.png',
    filters: [{ name: 'PNG Image', extensions: ['png'] }],
  });
  if (result.canceled || !result.filePath) return false;

  const base64 = dataUrl.replace(/^data:image\/png;base64,/, '');
  const buffer = Buffer.from(base64, 'base64');
  try { fs.writeFileSync(result.filePath, buffer); } catch { return false; }
  return result.filePath;
});

ipcMain.handle('save-svg', async (_event, svgString) => {
  if (typeof svgString !== 'string' || !svgString.startsWith('<?xml')) {
    return false;
  }
  const result = await dialog.showSaveDialog(mainWindow, {
    title: '导出 SVG',
    defaultPath: 'export.svg',
    filters: [{ name: 'SVG Image', extensions: ['svg'] }],
  });
  if (result.canceled || !result.filePath) return false;
  try { fs.writeFileSync(result.filePath, svgString, 'utf-8'); } catch { return false; }
  return result.filePath;
});

ipcMain.handle('read-file', async (_event, filePath) => {
  if (typeof filePath !== 'string') {
    throw new Error('invalid path');
  }
  const abs = path.resolve(filePath);
  if (!isPathAllowed(abs)) {
    throw new Error('path not allowed: ' + filePath);
  }
  const ext = path.extname(abs).toLowerCase();
  if (!_ALLOWED_EXTENSIONS.includes(ext)) {
    throw new Error('extension not allowed: ' + ext);
  }
  return await fs.promises.readFile(abs);
});

ipcMain.handle('file-exists', async (_event, filePath) => {
  if (typeof filePath !== 'string') return false;
  const abs = path.resolve(filePath);
  if (!isPathAllowed(abs)) {
    console.warn('[file-exists] path not in allowed roots: ' + abs);
    return false;
  }
  return fs.existsSync(abs);
});

ipcMain.handle('get-help-images-path', async () => {
  return getHelpImagesPath();
});

ipcMain.handle('list-help-images', async (_event, folder) => {
  const dir = getHelpImagesPath();
  if (!dir) return [];
  const folderPath = path.join(dir, folder);
  if (!fs.existsSync(folderPath)) return [];
  const files = fs.readdirSync(folderPath)
    .filter(f => /\.(png|jpg|jpeg)$/i.test(f))
    .sort((a, b) => {
      const na = parseInt(path.basename(a, path.extname(a)));
      const nb = parseInt(path.basename(b, path.extname(b)));
      return na - nb;
    });
  return files;
});

ipcMain.handle('read-help-image', async (_event, folder, filename) => {
  const dir = getHelpImagesPath();
  if (!dir) return null;
  const filePath = path.join(dir, folder, filename);
  if (!fs.existsSync(filePath)) return null;
  const buffer = fs.readFileSync(filePath);
  const ext = path.extname(filename).toLowerCase();
  const mime = ext === '.jpg' || ext === '.jpeg' ? 'image/jpeg' : 'image/png';
  return 'data:' + mime + ';base64,' + buffer.toString('base64');
});

// ── Batch Position Capture ──────────────────────────────

ipcMain.handle('select-batch-position-files', async () => {
  const bodyResult = await dialog.showOpenDialog(mainWindow, {
    title: '选择车壳 STP 文件',
    filters: [{ name: 'STEP 模型', extensions: ['stp', 'step'] }],
    properties: ['openFile'],
  });
  if (bodyResult.canceled || !bodyResult.filePaths[0]) return null;

  const outResult = await dialog.showOpenDialog(mainWindow, {
    title: '选择输出目录',
    properties: ['openDirectory'],
  });
  if (outResult.canceled || !outResult.filePaths[0]) return null;

  const xlsxResult = await dialog.showOpenDialog(mainWindow, {
    title: '选择 BOM Excel 文件（可多选）',
    filters: [{ name: 'Excel 文件', extensions: ['xlsx'] }],
    properties: ['openFile', 'multiSelections'],
  });
  if (xlsxResult.canceled || !xlsxResult.filePaths[0]) return null;

  return {
    bodyStpPath: bodyResult.filePaths[0],
    outputDir: outResult.filePaths[0],
    excelFiles: xlsxResult.filePaths.map(p => ({
      path: p,
      dir: path.dirname(p),
    })),
  };
});

ipcMain.handle('run-bom-preview-cached', async (_event, bomPath, modelsDir) => {
  if (!bomPath || !fs.existsSync(bomPath)) {
    safeSend('batch-pipeline-progress', 'ERROR: BOM file not found: ' + bomPath);
    return null;
  }

  const modelsDirResolved = modelsDir || path.dirname(bomPath);
  const outputDir = _tsOutputDir(bomPath.replace(/\\/g, '/').replace(/\.xlsx$/i, ''));
  registerAllowedRoot(outputDir);
  registerAllowedRoot(modelsDirResolved);
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    bomPath,
    '--bom', bomPath,
    '--models-dir', modelsDirResolved,
    '--output-dir', outputDir,
    '--preview',
  ];

  safeSend('batch-pipeline-progress', '=== BOM 管线启动: ' + path.basename(bomPath) + ' ===');

  return new Promise((resolve) => {
    const env = buildPipelineEnv();
    const proc = spawnPipeline(exePath, args, env);

    proc.on('close', (code) => {
      if (code === 0) {
        const jsonPath = path.join(outputDir, 'assembly.json');
        safeSend('batch-pipeline-progress', '管线完成: ' + path.basename(bomPath));
        resolve(jsonPath);
      } else {
        safeSend('batch-pipeline-progress', '管线失败 (code ' + code + '): ' + path.basename(bomPath));
        resolve(null);
      }
    });
  });
});

ipcMain.handle('save-batch-file', async (_event, filePath, opts) => {
  try {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
  } catch {}
  const buffer = Buffer.from(opts.data, opts.encoding || 'base64');
  fs.writeFileSync(filePath, buffer);
  return filePath;
});

ipcMain.handle('save-batch-svg', async (_event, filePath, svgString) => {
  try {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
  } catch {}
  fs.writeFileSync(filePath, svgString, 'utf-8');
  return filePath;
});

// ── Body shell management ─────────────────────────────────

ipcMain.handle('list-user-bodies', async () => {
  if (!fs.existsSync(getUserBodiesDir())) {
    fs.mkdirSync(getUserBodiesDir(), { recursive: true });
    return [];
  }
  const files = fs.readdirSync(getUserBodiesDir()).filter(f => f.endsWith('.glb'));
  return files.map(f => ({
    name: path.basename(f, '.glb'),
    glb: 'local:///' + path.join(getUserBodiesDir(), f).replace(/\\/g, '/'),
  }));
});

ipcMain.handle('import-body', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择车壳 STP 文件',
    filters: [{ name: 'STEP 模型', extensions: ['stp', 'step'] }],
    properties: ['openFile'],
  });
  if (result.canceled || !result.filePaths[0]) return null;

  const stpPath = result.filePaths[0];
  const { exePath, baseArgs } = findPipelineExe();

  if (!fs.existsSync(getUserBodiesDir())) {
    fs.mkdirSync(getUserBodiesDir(), { recursive: true });
  }

  const args = [
    ...baseArgs,
    stpPath,
    '--export-body',
    '--output-dir', getUserBodiesDir(),
  ];

  safeSend('pipeline-progress', '=== 导入车壳 ===');

  return new Promise((resolve) => {
    const env = buildPipelineEnv();
    const proc = spawnPipeline(exePath, args, env);

    proc.on('close', (code) => {
      if (code === 0) {
        const bodyName = path.basename(stpPath, path.extname(stpPath));
        resolve({ name: bodyName, ok: true });
      } else {
        resolve({ name: '', ok: false });
      }
    });
  });
});

ipcMain.handle('import-body-from-path', async (_event, stpPath) => {
  if (typeof stpPath !== 'string' || !fs.existsSync(stpPath)) return { name: '', ok: false };
  const { exePath, baseArgs } = findPipelineExe();

  if (!fs.existsSync(getUserBodiesDir())) {
    fs.mkdirSync(getUserBodiesDir(), { recursive: true });
  }

  const args = [
    ...baseArgs,
    stpPath,
    '--export-body',
    '--output-dir', getUserBodiesDir(),
  ];

  safeSend('pipeline-progress', '=== 导入车壳 (批量) ===');

  return new Promise((resolve) => {
    const env = buildPipelineEnv();
    const proc = spawnPipeline(exePath, args, env);

    proc.on('close', (code) => {
      if (code === 0) {
        const bodyName = path.basename(stpPath, path.extname(stpPath));
        resolve({ name: bodyName, ok: true });
      } else {
        resolve({ name: '', ok: false });
      }
    });
  });
});

// ── Pipeline: Preview STP (renderer-initiated) ───────────

ipcMain.handle('run-preview-pipeline', async () => {
  await runPreviewPipeline();
});

// ── BOM Pipeline: Preview (BOM → mesh + glb) ──────────────

ipcMain.handle('run-bom-preview-pipeline', async () => {
  await runBomPreviewPipeline();
});

// ── BOM Pipeline: Full (BOM → contacts → DAG) ────────────

ipcMain.handle('run-bom-full-pipeline', async (_event, targetPart) => {
  await runBomFullPipeline(targetPart || null);
});

// ── BOM Pipeline: Full Cached (no file dialog) ────────────

ipcMain.handle('run-bom-full-pipeline-cached', async (_event, bomPath, modelsDir, targetPart) => {
  await runBomFullPipelineCached(bomPath, modelsDir, targetPart || null);
});

// ── Pipeline scoped to a sub-assembly node ─────────────────

ipcMain.handle('run-pipeline-for-node-cached', async (_event, stpPath, rootNode, compoundsJson) => {
  if (typeof stpPath !== 'string' || !fs.existsSync(stpPath)) return;
  const ext = path.extname(stpPath).toLowerCase();
  if (ext !== '.stp' && ext !== '.step') {
    safeSend('pipeline-progress', 'ERROR: invalid STP extension: ' + ext);
    return;
  }
  if (!validatePartName(rootNode)) {
    safeSend('pipeline-progress', 'ERROR: invalid rootNode name');
    return;
  }

  const outputDir = _tsOutputDir(stpPath);
  registerAllowedRoot(outputDir);
  registerAllowedRoot(path.dirname(stpPath));
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    stpPath,
    '--output-dir', outputDir,
    '--root-node', rootNode,
  ];

  if (compoundsJson && compoundsJson !== '[]' && compoundsJson !== '') {
    args.push('--compounds', compoundsJson);
  }

  safeSend('pipeline-progress', '=== 生成拆卸方案 (节点: ' + rootNode + ') ===');
  safeSend('pipeline-mode', 'full');
  safeSend('pipeline-started', stpPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      safeSend('pipeline-complete', jsonPath);
    } else {
      safeSend('pipeline-progress', '管线执行失败，退出码: ' + code);
      safeSend('pipeline-error', code);
    }
  });
});

// ── Single-file STP Pipeline: dependency chain for a target part ─

ipcMain.handle('run-single-pipeline-chain', async (_event, stpPath, targetPart, compoundsJson) => {
  if (typeof stpPath !== 'string' || !fs.existsSync(stpPath)) {
    safeSend('pipeline-progress', 'ERROR: STP file not found: ' + stpPath);
    return;
  }
  const ext = path.extname(stpPath).toLowerCase();
  if (ext !== '.stp' && ext !== '.step') {
    safeSend('pipeline-progress', 'ERROR: invalid STP extension: ' + ext);
    return;
  }
  if (!validatePartName(targetPart)) {
    safeSend('pipeline-progress', 'ERROR: invalid targetPart name');
    return;
  }

  const outputDir = _tsOutputDir(stpPath);
  registerAllowedRoot(outputDir);
  registerAllowedRoot(path.dirname(stpPath));
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    stpPath,
    '--output-dir', outputDir,
    '--target-part', targetPart,
  ];

  if (compoundsJson && compoundsJson !== '[]' && compoundsJson !== '') {
    args.push('--compounds', compoundsJson);
  }

  safeSend('pipeline-progress', '=== 依赖链分析 (目标: ' + targetPart + ') ===');
  safeSend('pipeline-mode', 'chain');
  safeSend('pipeline-started', stpPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      safeSend('pipeline-complete', jsonPath);
    } else {
      safeSend('pipeline-progress', '管线执行失败，退出码: ' + code);
      safeSend('pipeline-error', code);
    }
  });
});

ipcMain.handle('run-pipeline-for-node', async (_event, rootNode) => {
  if (!validatePartName(rootNode)) {
    safeSend('pipeline-progress', 'ERROR: invalid rootNode name');
    return;
  }
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择 STP 数模文件 (将仅处理节点: ' + (rootNode || '全部') + ')',
    filters: [{ name: 'STEP 模型', extensions: ['stp', 'step'] }],
    properties: ['openFile'],
  });
  if (result.canceled || !result.filePaths[0]) return;

  const stpPath = result.filePaths[0];
  const outputDir = _tsOutputDir(stpPath);
  registerAllowedRoot(outputDir);
  registerAllowedRoot(path.dirname(stpPath));
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    stpPath,
    '--output-dir', outputDir,
    '--root-node', rootNode,
  ];

  safeSend('pipeline-progress', '=== 生成拆卸方案 (节点: ' + rootNode + ') ===');
  safeSend('pipeline-mode', 'full');
  safeSend('pipeline-started', stpPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      safeSend('pipeline-complete', jsonPath);
    } else {
      safeSend('pipeline-progress', '管线执行失败，退出码: ' + code);
      safeSend('pipeline-error', code);
    }
  });
});

// ── Explosion Pipeline (geometric-only) with optional center part ─

ipcMain.handle('run-explosion-pipeline-with-center', async (_event, stpPath, centerPart) => {
  if (typeof stpPath !== 'string' || !fs.existsSync(stpPath)) {
    safeSend('pipeline-progress', 'ERROR: STP file not found: ' + stpPath);
    return;
  }
  const ext = path.extname(stpPath).toLowerCase();
  if (ext !== '.stp' && ext !== '.step') {
    safeSend('pipeline-progress', 'ERROR: invalid STP extension: ' + ext);
    return;
  }
  if (centerPart != null && !validatePartName(centerPart)) {
    safeSend('pipeline-progress', 'ERROR: invalid centerPart name');
    return;
  }

  const outputDir = _tsOutputDir(stpPath);
  registerAllowedRoot(outputDir);
  registerAllowedRoot(path.dirname(stpPath));
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    stpPath,
    '--output-dir', outputDir,
    '--skip-collision',
  ];
  if (centerPart) {
    args.push('--center-part', centerPart);
  }

  const centerLabel = centerPart ? ' (中心: ' + centerPart + ')' : ' (几何重心)';
  safeSend('pipeline-progress', '=== 生成爆炸视图' + centerLabel + ' ===');
  safeSend('pipeline-mode', 'full');
  safeSend('pipeline-started', stpPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      safeSend('pipeline-complete', jsonPath);
    } else {
      safeSend('pipeline-progress', '爆炸视图生成失败，退出码: ' + code);
      safeSend('pipeline-error', code);
    }
  });
});

ipcMain.handle('run-bom-explosion-pipeline-cached', async (_event, bomPath, modelsDir, centerPart) => {
  if (!bomPath || !fs.existsSync(bomPath)) {
    safeSend('pipeline-progress', 'ERROR: BOM file not found: ' + bomPath);
    return;
  }
  if (centerPart != null && !validatePartName(centerPart)) {
    safeSend('pipeline-progress', 'ERROR: invalid centerPart name');
    return;
  }

  const modelsDirResolved = modelsDir || path.dirname(bomPath);
  const outputDir = _tsOutputDir(bomPath.replace(/\\/g, '/').replace(/\.xlsx$/i, ''));
  registerAllowedRoot(outputDir);
  registerAllowedRoot(modelsDirResolved);
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    bomPath,
    '--bom', bomPath,
    '--models-dir', modelsDirResolved,
    '--output-dir', outputDir,
    '--skip-collision',
  ];
  if (centerPart) {
    args.push('--center-part', centerPart);
  }

  const centerLabel = centerPart ? ' (中心: ' + centerPart + ')' : ' (几何重心)';
  safeSend('pipeline-progress', '=== BOM 爆炸视图' + centerLabel + ' ===');
  safeSend('pipeline-mode', 'bom-full');
  safeSend('pipeline-started', bomPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      safeSend('pipeline-complete', jsonPath);
    } else {
      safeSend('pipeline-progress', 'BOM 爆炸视图失败，退出码: ' + code);
      safeSend('pipeline-error', code);
    }
  });
});

// ── Pipeline: Model Cleaning (STP + XLSX → clean assembly) ──

ipcMain.handle('run-clean-pipeline', async () => {
  await runCleanPipeline();
});

ipcMain.handle('run-compare-pipeline', async () => {
  await runComparePipeline();
});

ipcMain.handle('run-pmi-match', async (_event, stpPath) => {
  return await runPmiMatch(stpPath);
});

// ── Pipeline: Preview STP (mesh + load, no analysis) ────

async function runPreviewPipeline() {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择 STP 数模预览',
    filters: [{ name: 'STEP 模型', extensions: ['stp', 'step'] }],
    properties: ['openFile'],
  });
  if (result.canceled || !result.filePaths[0]) return;

  const stpPath = result.filePaths[0];
  const outputDir = _tsOutputDir(stpPath, 'preview');
  registerAllowedRoot(outputDir);
  registerAllowedRoot(path.dirname(stpPath));
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    stpPath,
    '--output-dir', outputDir,
    '--preview',
  ];

  safeSend('pipeline-progress', '=== 导入 STP 预览 ===');
  safeSend('pipeline-mode', 'preview');
  safeSend('pipeline-started', stpPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      safeSend('pipeline-complete', jsonPath);
    } else {
      safeSend('pipeline-progress', '预览失败，退出码: ' + code);
    }
  });
}

// ── Pipeline: Import STP → Generate Disassembly Plan ─────

function findPipelineExe() {
  if (app.isPackaged) {
    const exePath = path.join(process.resourcesPath, 'pipeline', 'AutoModel.exe');
    if (fs.existsSync(exePath)) {
      return { exePath, baseArgs: [] };
    }
  }
  const python = findPython();
  const script = path.join(__dirname, 'pipeline.py');
  return { exePath: python, baseArgs: [script] };
}

function findPython() {
  const candidates = [
    path.join(process.env.USERPROFILE || '', 'miniconda3', 'envs', 'pyoccenv', 'python.exe'),
    path.join(process.env.USERPROFILE || '', 'Anaconda3', 'envs', 'pyoccenv', 'python.exe'),
    'python',
    'python3',
  ];
  for (const c of candidates) {
    if (c === 'python' || c === 'python3') return c;
    if (fs.existsSync(c)) return c;
  }
  return 'python';
}

function _tsOutputDir(stpPath, prefix = 'output') {
  const ts = new Date().toISOString()
    .replace(/[T:]/g, '_').replace(/\..+/, '').replace(/-/g, '').substring(2);
  // Append a millis suffix to avoid collisions within same second
  const suffix = String(Date.now() % 100000).padStart(5, '0');
  return path.join(path.dirname(stpPath), prefix + '_' + ts + '_' + suffix);
}

function buildPipelineEnv() {
  const env = { ...process.env };
  const pyoccBin = path.join(
    process.env.USERPROFILE || '',
    'miniconda3', 'envs', 'pyoccenv', 'Library', 'bin');
  if (fs.existsSync(pyoccBin)) {
    env.PATH = pyoccBin + ';' + (env.PATH || '');
  }
  // Force UTF-8 + unbuffered I/O so pipeline progress lines arrive promptly and intact
  env.PYTHONIOENCODING = 'utf-8';
  env.PYTHONUNBUFFERED = '1';
  env.PYTHONLEGACYWINDOWSSTDIO = 'utf-8';
  return env;
}

// ── Subprocess management ─────────────────────────────────

const _runningProcs = new Set();

function spawnPipeline(exePath, args, env) {
  const proc = spawn(exePath, args, { env, windowsHide: true });
  _runningProcs.add(proc);

  proc.on('exit', () => _runningProcs.delete(proc));
  proc.on('error', (err) => {
    _runningProcs.delete(proc);
    safeSend('pipeline-progress', '[ERR] spawn failed: ' + (err && err.message));
    safeSend('pipeline-error', -1);
  });

  const stdoutFlush = (() => {
    let buf = '';
    return {
      handler: (chunk) => {
        buf += chunk.toString('utf8');
        const lines = buf.split(/\r?\n/);
        buf = lines.pop();
        for (const line of lines) {
          if (line.length === 0) continue;
          safeSend('pipeline-progress', line);
        }
      },
      flush: () => {
        if (buf) {
          safeSend('pipeline-progress', buf);
          buf = '';
        }
      },
    };
  })();
  const stderrFlush = (() => {
    let buf = '';
    return {
      handler: (chunk) => {
        buf += chunk.toString('utf8');
        const lines = buf.split(/\r?\n/);
        buf = lines.pop();
        for (const line of lines) {
          if (line.length === 0) continue;
          safeSend('pipeline-progress', '[ERR] ' + line);
        }
      },
      flush: () => {
        if (buf) {
          safeSend('pipeline-progress', '[ERR] ' + buf);
          buf = '';
        }
      },
    };
  })();

  proc.stdout.on('data', stdoutFlush.handler);
  proc.stderr.on('data', stderrFlush.handler);
  proc.on('close', () => {
    stdoutFlush.flush();
    stderrFlush.flush();
  });

  return proc;
}

function killAllPipelines() {
  for (const p of _runningProcs) {
    try { p.kill('SIGTERM'); } catch {}
  }
  setTimeout(() => {
    for (const p of _runningProcs) {
      try { p.kill('SIGKILL'); } catch {}
    }
  }, 3000);
}

async function runExplosionPipeline() {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择 STP 数模文件 (生成爆炸路径)',
    filters: [{ name: 'STEP 模型', extensions: ['stp', 'step'] }],
    properties: ['openFile'],
  });
  if (result.canceled || !result.filePaths[0]) return;

  const stpPath = result.filePaths[0];
  const outputDir = _tsOutputDir(stpPath);
  registerAllowedRoot(outputDir);
  registerAllowedRoot(path.dirname(stpPath));
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    stpPath,
    '--output-dir', outputDir,
    '--skip-collision',
  ];

  safeSend('pipeline-progress', '=== 生成爆炸路径 ===');
  safeSend('pipeline-mode', 'full');
  safeSend('pipeline-started', stpPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      safeSend('pipeline-complete', jsonPath);
    } else {
      safeSend('pipeline-progress', '管线执行失败，退出码: ' + code);
      safeSend('pipeline-error', code);
    }
  });
}

async function runImportPipeline() {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择 STP 数模文件 (生成拆装方案)',
    filters: [{ name: 'STEP 模型', extensions: ['stp', 'step'] }],
    properties: ['openFile'],
  });
  if (result.canceled || !result.filePaths[0]) return;

  const stpPath = result.filePaths[0];
  const outputDir = _tsOutputDir(stpPath);
  registerAllowedRoot(outputDir);
  registerAllowedRoot(path.dirname(stpPath));
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    stpPath,
    '--output-dir', outputDir,
  ];

  safeSend('pipeline-progress', '=== 导入 STP 生成拆装方案 ===');
  safeSend('pipeline-mode', 'full');
  safeSend('pipeline-started', stpPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      safeSend('pipeline-complete', jsonPath);
    } else {
      safeSend('pipeline-progress', '管线执行失败，退出码: ' + code);
      safeSend('pipeline-error', code);
    }
  });
}

// ── BOM Pipeline: Preview ──────────────────────────────────

async function runBomPreviewPipeline() {
  const bomResult = await dialog.showOpenDialog(mainWindow, {
    title: '选择 BOM Excel 文件',
    filters: [{ name: 'Excel 文件', extensions: ['xlsx'] }],
    properties: ['openFile'],
  });
  if (bomResult.canceled || !bomResult.filePaths[0]) return;

  const bomPath = bomResult.filePaths[0];
  const modelsDir = path.dirname(bomPath);

  const outputDir = _tsOutputDir(bomPath.replace(/\\/g, '/').replace(/\.xlsx$/i, ''));
  registerAllowedRoot(outputDir);
  registerAllowedRoot(modelsDir);
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    bomPath,
    '--bom', bomPath,
    '--models-dir', modelsDir,
    '--output-dir', outputDir,
    '--preview',
  ];

  safeSend('pipeline-progress', '=== BOM 预览加载 ===');
  safeSend('pipeline-mode', 'bom-preview');
  safeSend('pipeline-started', bomPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      safeSend('pipeline-complete', jsonPath);
    } else {
      safeSend('pipeline-progress', 'BOM预览失败，退出码: ' + code);
    }
  });
}

// ── BOM Pipeline: Full (contacts + DAG + optional dependency chain) ─

async function runBomFullPipeline(targetPart) {
  if (targetPart != null && !validatePartName(targetPart)) {
    safeSend('pipeline-progress', 'ERROR: invalid targetPart name');
    return;
  }
  const bomResult = await dialog.showOpenDialog(mainWindow, {
    title: '选择 BOM Excel 文件',
    filters: [{ name: 'Excel 文件', extensions: ['xlsx'] }],
    properties: ['openFile'],
  });
  if (bomResult.canceled || !bomResult.filePaths[0]) return;

  const bomPath = bomResult.filePaths[0];
  const modelsDir = path.dirname(bomPath);

  const outputDir = _tsOutputDir(bomPath.replace(/\\/g, '/').replace(/\.xlsx$/i, ''));
  registerAllowedRoot(outputDir);
  registerAllowedRoot(modelsDir);
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    bomPath,
    '--bom', bomPath,
    '--models-dir', modelsDir,
    '--output-dir', outputDir,
  ];

  if (targetPart) {
    args.push('--target-part', targetPart);
  }

  const modeLabel = targetPart
    ? 'BOM 依赖链分析: ' + targetPart
    : 'BOM 完整拆卸方案';

  safeSend('pipeline-progress', '=== ' + modeLabel + ' ===');
  safeSend('pipeline-mode', 'bom-full');
  safeSend('pipeline-started', bomPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      safeSend('pipeline-complete', jsonPath);
    } else {
      safeSend('pipeline-progress', 'BOM管线失败，退出码: ' + code);
      safeSend('pipeline-error', code);
    }
  });
}

// ── BOM Pipeline: Full Cached (reuses paths, no file dialog) ─

async function runBomFullPipelineCached(bomPath, modelsDir, targetPart) {
  if (!bomPath || !fs.existsSync(bomPath)) {
    safeSend('pipeline-progress', 'ERROR: BOM file not found: ' + bomPath);
    return;
  }
  if (targetPart != null && !validatePartName(targetPart)) {
    safeSend('pipeline-progress', 'ERROR: invalid targetPart name');
    return;
  }

  const modelsDirResolved = modelsDir || path.dirname(bomPath);
  const outputDir = _tsOutputDir(bomPath.replace(/\\/g, '/').replace(/\.xlsx$/i, ''));
  registerAllowedRoot(outputDir);
  registerAllowedRoot(modelsDirResolved);
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    bomPath,
    '--bom', bomPath,
    '--models-dir', modelsDirResolved,
    '--output-dir', outputDir,
  ];

  if (targetPart) {
    args.push('--target-part', targetPart);
  }

  const modeLabel = targetPart
    ? 'BOM 依赖链分析: ' + targetPart
    : 'BOM 完整拆卸方案';

  safeSend('pipeline-progress', '=== ' + modeLabel + ' ===');
  safeSend('pipeline-mode', 'bom-full');
  safeSend('pipeline-started', bomPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      safeSend('pipeline-complete', jsonPath);
    } else {
      safeSend('pipeline-progress', 'BOM管线失败，退出码: ' + code);
      safeSend('pipeline-error', code);
    }
  });
}

// ── Pipeline: Model Cleaning (STP + XLSX → clean assembly) ──

async function runCleanPipeline() {
  const stpResult = await dialog.showOpenDialog(mainWindow, {
    title: '选择 STP 数模文件',
    filters: [{ name: 'STEP 模型', extensions: ['stp', 'step'] }],
    properties: ['openFile'],
  });
  if (stpResult.canceled || !stpResult.filePaths[0]) return;

  const stpPath = stpResult.filePaths[0];

  const xlsxResult = await dialog.showOpenDialog(mainWindow, {
    title: '选择 BOM 表格 (.xlsx)',
    filters: [{ name: 'Excel 表格', extensions: ['xlsx'] }],
    properties: ['openFile'],
  });
  if (xlsxResult.canceled || !xlsxResult.filePaths[0]) return;

  const xlsxPath = xlsxResult.filePaths[0];

  const outputDir = _tsOutputDir(stpPath, 'clean');
  registerAllowedRoot(outputDir);
  registerAllowedRoot(path.dirname(stpPath));
  registerAllowedRoot(path.dirname(xlsxPath));
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    stpPath,
    '--output-dir', outputDir,
    '--clean',
    '--clean-bom', xlsxPath,
  ];

  safeSend('pipeline-progress', '=== 数模清洗 ===');
  safeSend('pipeline-progress', 'STP: ' + stpPath);
  safeSend('pipeline-progress', 'BOM: ' + xlsxPath);
  safeSend('pipeline-mode', 'clean');
  safeSend('pipeline-started', stpPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      safeSend('pipeline-complete', jsonPath);
    } else {
      safeSend('pipeline-progress', '数模清洗失败，退出码: ' + code);
      safeSend('pipeline-error', code);
    }
  });
}

async function runPmiMatch(stpPath) {
  if (!stpPath || !fs.existsSync(stpPath)) {
    safeSend('pipeline-progress', 'ERROR: STP file not found: ' + stpPath);
    return null;
  }
  registerAllowedRoot(path.dirname(stpPath));
  const outputDir = _tsOutputDir(stpPath, 'pmi');
  registerAllowedRoot(outputDir);
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    stpPath,
    '--output-dir', outputDir,
    '--pmi',
  ];

  safeSend('pipeline-progress', '=== PMI 匹配 ===');
  safeSend('pipeline-mode', 'pmi-match');
  safeSend('pipeline-started', stpPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  return new Promise((resolve) => {
    proc.on('close', (code) => {
      if (code === 0) {
        const jsonPath = path.join(outputDir, 'pmi_labels.json');
        if (fs.existsSync(jsonPath)) {
          try {
            const data = JSON.parse(fs.readFileSync(jsonPath, 'utf-8'));
            resolve(data);
          } catch (e) {
            resolve(null);
          }
        } else {
          resolve(null);
        }
      } else {
        resolve(null);
      }
    });
  });
}

async function runComparePipeline() {
  const xlsxResult = await dialog.showOpenDialog(mainWindow, {
    title: '选择对比表格 (.xlsx, Sheet3=A列/B列)',
    filters: [{ name: 'Excel 表格', extensions: ['xlsx'] }],
    properties: ['openFile'],
  });
  if (xlsxResult.canceled || !xlsxResult.filePaths[0]) return;

  const xlsxPath = xlsxResult.filePaths[0];
  const modelsDir = path.dirname(xlsxPath);

  const outputDir = _tsOutputDir(xlsxPath.replace(/\\/g, '/').replace(/\.xlsx$/i, ''), 'compare');
  registerAllowedRoot(outputDir);
  registerAllowedRoot(modelsDir);
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    xlsxPath,
    '--compare', xlsxPath,
    '--models-dir', modelsDir,
    '--output-dir', outputDir,
  ];

  safeSend('pipeline-progress', '=== 数模对比 ===');
  safeSend('pipeline-progress', '对比表格: ' + xlsxPath);
  safeSend('pipeline-progress', '模型目录: ' + modelsDir);
  safeSend('pipeline-mode', 'compare');
  safeSend('pipeline-started', xlsxPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      safeSend('pipeline-progress', '数模对比完成');
      safeSend('pipeline-complete', outputDir);
    } else {
      safeSend('pipeline-progress', '数模对比失败，退出码: ' + code);
      safeSend('pipeline-error', code);
    }
  });
}

// ── Pipeline: Validate Disassembly Paths (Collision Check) ─

async function runValidatePipeline() {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择已有的 assembly.json 进行碰撞验证',
    filters: [{ name: 'Assembly JSON', extensions: ['json'] }],
    properties: ['openFile'],
  });
  if (result.canceled || !result.filePaths[0]) return;

  const jsonPath = result.filePaths[0];
  const outputDir = path.dirname(jsonPath);
  registerAllowedRoot(outputDir);
  const { exePath, baseArgs } = findPipelineExe();

  const args = [
    ...baseArgs,
    jsonPath,
    '--output-dir', outputDir,
    '--validate',
  ];

  safeSend('pipeline-progress', '=== 验证拆卸路径 (碰撞检测) ===');
  safeSend('pipeline-progress', '输入: ' + jsonPath);

  const env = buildPipelineEnv();
  const proc = spawnPipeline(exePath, args, env);

  proc.on('close', (code) => {
    if (code === 0) {
      safeSend('pipeline-progress', '碰撞验证完成');
      safeSend('pipeline-complete', jsonPath);
    } else {
      safeSend('pipeline-progress', '验证失败，退出码: ' + code);
    }
  });
}

// ── Register custom 'local' scheme for safe local file loading ──
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'local',
    privileges: { standard: true, secure: true, stream: true, supportFetchAPI: true },
  },
]);

// ── App Lifecycle ─────────────────────────────────────────

app.whenReady().then(() => {
  // Ensure user bodies directory exists
  if (!fs.existsSync(getUserBodiesDir())) {
    fs.mkdirSync(getUserBodiesDir(), { recursive: true });
  }

  // Register baseline allowed roots
  registerAllowedRoot(getUserBodiesDir());
  registerAllowedRoot(path.join(__dirname, 'bodies'));
  if (app.isPackaged) {
    registerAllowedRoot(path.join(process.resourcesPath, 'pipeline'));
  } else {
    registerAllowedRoot(__dirname);
  }

  // Handle local:// protocol — serves files from disk, restricted to allowed roots
  protocol.handle('local', (request) => {
    try {
      const url = new URL(request.url);
      const drive = url.hostname ? url.hostname.toUpperCase() + ':\\' : '';
      let p = decodeURIComponent(url.pathname || '');
      if (p.startsWith('/')) p = p.slice(1);
      const abs = path.normalize(drive + p);
      // Reject UNC paths
      if (abs.startsWith('\\\\') || abs.startsWith('//')) {
        return new Response('UNC paths not allowed', { status: 403 });
      }
      if (!path.isAbsolute(abs)) {
        return new Response('absolute path required', { status: 400 });
      }
      if (!isPathAllowed(abs)) {
        return new Response('path not allowed', { status: 403 });
      }
      return net.fetch('file:///' + abs.replace(/\\/g, '/'));
    } catch (err) {
      return new Response('bad request: ' + (err && err.message), { status: 400 });
    }
  });

  buildMenu();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  killAllPipelines();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
