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
          label: '导入装配数据 (assembly.json)',
          accelerator: 'CmdOrCtrl+O',
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
          label: '导入 BOM 预览 (多文件)',
          accelerator: 'CmdOrCtrl+B',
          click: () => runBomPreviewPipeline(),
        },
        {
          label: '导入 STP 预览',
          accelerator: 'CmdOrCtrl+I',
          click: () => runPreviewPipeline(),
        },
        {
          label: '生成拆卸方案',
          accelerator: 'CmdOrCtrl+G',
          click: () => runImportPipeline(),
        },
        { type: 'separator' },
        {
          label: '验证拆卸路径 (碰撞检测)',
          accelerator: 'CmdOrCtrl+Shift+V',
          click: () => runValidatePipeline(),
        },
      ],
    },
    {
      label: '视图',
      submenu: [
        {
          label: '复位视角',
          accelerator: 'F',
          click: () => safeSend('menu-reset-camera'),
        },
        {
          label: '切换位置图模式',
          click: () => safeSend('menu-toggle-ghost'),
        },
        { type: 'separator' },
        {
          label: '显示/隐藏标注',
          click: () => safeSend('menu-toggle-annotations'),
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
          click: () => {
            if (mainWindow && !mainWindow.isDestroyed()) {
              dialog.showMessageBox(mainWindow, {
                type: 'info',
                title: '关于',
                message: '数模自动拆装工具\n基于 OpenCASCADE + Three.js + Electron\n版本 2.0',
              }).catch(() => {});
            }
          },
        },
      ],
    },
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
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
  fs.writeFileSync(result.filePath, buffer);
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

ipcMain.handle('run-pipeline-for-node-cached', async (_event, stpPath, rootNode) => {
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

  const lineHandler = (channel, prefix = '') => {
    let buf = '';
    return (chunk) => {
      try {
        buf += chunk.toString('utf8');
        const lines = buf.split(/\r?\n/);
        buf = lines.pop();
        for (const line of lines) {
          if (line.length === 0) continue;
          safeSend(channel, prefix + line);
        }
      } catch (e) {
        // ignore decoding hiccups
      }
    };
  };
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

async function runImportPipeline() {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择 STP 数模文件',
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

  safeSend('pipeline-progress', '=== 导入 STP 生成拆卸方案 ===');
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
