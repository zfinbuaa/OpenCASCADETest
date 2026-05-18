/**
 * Electron Main Process — 窗口管理、菜单、IPC、Python 管线子进程
 */

const { app, BrowserWindow, Menu, dialog, ipcMain, protocol, net } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

let mainWindow = null;

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
    },
    title: '整车数模自动拆装方案系统',
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
          click: () => mainWindow.webContents.send('menu-load-assembly'),
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
          click: () => mainWindow.webContents.send('menu-reset-camera'),
        },
        {
          label: '切换位置图模式',
          click: () => mainWindow.webContents.send('menu-toggle-ghost'),
        },
        { type: 'separator' },
        {
          label: '显示/隐藏标注',
          click: () => mainWindow.webContents.send('menu-toggle-annotations'),
        },
      ],
    },
    {
      label: '导出',
      submenu: [
        {
          label: '截图当前视图',
          accelerator: 'CmdOrCtrl+S',
          click: () => mainWindow.webContents.send('menu-screenshot'),
        },
        {
          label: '逐阶段批量截图',
          click: () => mainWindow.webContents.send('menu-batch-capture'),
        },
      ],
    },
    {
      label: '帮助',
      submenu: [
        {
          label: '关于',
          click: () => {
            dialog.showMessageBox(mainWindow, {
              type: 'info',
              title: '关于',
              message: '整车三维数模自动拆装方案生成系统\n基于 OpenCASCADE + Three.js + Electron\n版本 2.0',
            });
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
  return { filePath, content, dir };
});

ipcMain.handle('save-screenshot', async (_event, dataUrl) => {
  const result = await dialog.showSaveDialog(mainWindow, {
    title: '保存截图',
    defaultPath: 'screenshot.png',
    filters: [{ name: 'PNG Image', extensions: ['png'] }],
  });
  if (result.canceled || !result.filePath) return false;

  // dataUrl format: "data:image/png;base64,...."
  const base64 = dataUrl.replace(/^data:image\/png;base64,/, '');
  const buffer = Buffer.from(base64, 'base64');
  fs.writeFileSync(result.filePath, buffer);
  return result.filePath;
});

ipcMain.handle('read-file', async (_event, filePath) => {
  return fs.readFileSync(filePath);
});

ipcMain.handle('file-exists', async (_event, filePath) => {
  return fs.existsSync(filePath);
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
  const outputDir = path.join(path.dirname(stpPath), 'preview_output');
  const python = findPython();

  const args = [
    path.join(__dirname, 'pipeline.py'),
    stpPath,
    '--output-dir', outputDir,
    '--preview',
  ];

  mainWindow.webContents.send('pipeline-progress', '=== 导入 STP 预览 ===');
  mainWindow.webContents.send('pipeline-mode', 'preview');
  mainWindow.webContents.send('pipeline-started', stpPath);

  const env = Object.assign({}, process.env);
  const proc = spawn(python, args, { env });

  proc.stdout.on('data', (data) => {
    const lines = data.toString().split('\n').filter(Boolean);
    for (const line of lines) {
      mainWindow.webContents.send('pipeline-progress', line);
    }
  });

  proc.stderr.on('data', (data) => {
    mainWindow.webContents.send('pipeline-progress', '[ERR] ' + data.toString().trim());
  });

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      mainWindow.webContents.send('pipeline-complete', jsonPath);
    } else {
      mainWindow.webContents.send('pipeline-progress', '预览失败，退出码: ' + code);
    }
  });
}

// ── Pipeline: Import STP → Generate Disassembly Plan ─────

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

async function runImportPipeline() {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择 STP 数模文件',
    filters: [{ name: 'STEP 模型', extensions: ['stp', 'step'] }],
    properties: ['openFile'],
  });
  if (result.canceled || !result.filePaths[0]) return;

  const stpPath = result.filePaths[0];
  const outputDir = path.join(path.dirname(stpPath), 'output');
  const python = findPython();

  const args = [
    path.join(__dirname, 'pipeline.py'),
    stpPath,
    '--output-dir', outputDir,
    '--skip-collision',
  ];

  mainWindow.webContents.send('pipeline-progress', '=== 导入 STP 生成拆卸方案 ===');
  mainWindow.webContents.send('pipeline-mode', 'full');
  mainWindow.webContents.send('pipeline-started', stpPath);

  const env = Object.assign({}, process.env);
  const proc = spawn(python, args, { env });

  proc.stdout.on('data', (data) => {
    const lines = data.toString().split('\n').filter(Boolean);
    for (const line of lines) {
      mainWindow.webContents.send('pipeline-progress', line);
    }
  });

  proc.stderr.on('data', (data) => {
    mainWindow.webContents.send('pipeline-progress', '[ERR] ' + data.toString().trim());
  });

  proc.on('close', (code) => {
    if (code === 0) {
      const jsonPath = path.join(outputDir, 'assembly.json');
      mainWindow.webContents.send('pipeline-complete', jsonPath);
    } else {
      mainWindow.webContents.send('pipeline-progress', '管线执行失败，退出码: ' + code);
      mainWindow.webContents.send('pipeline-error', code);
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
  const python = findPython();

  const args = [
    path.join(__dirname, 'pipeline.py'),
    jsonPath,
    '--output-dir', outputDir,
    '--validate',
  ];

  mainWindow.webContents.send('pipeline-progress', '=== 验证拆卸路径 (碰撞检测) ===');
  mainWindow.webContents.send('pipeline-progress', '输入: ' + jsonPath);

  const env = Object.assign({}, process.env);
  const proc = spawn(python, args, { env });

  proc.stdout.on('data', (data) => {
    const lines = data.toString().split('\n').filter(Boolean);
    for (const line of lines) {
      mainWindow.webContents.send('pipeline-progress', line);
    }
  });

  proc.stderr.on('data', (data) => {
    mainWindow.webContents.send('pipeline-progress', '[ERR] ' + data.toString().trim());
  });

  proc.on('close', (code) => {
    if (code === 0) {
      mainWindow.webContents.send('pipeline-progress', '碰撞验证完成');
      mainWindow.webContents.send('pipeline-complete', jsonPath);
    } else {
      mainWindow.webContents.send('pipeline-progress', '验证失败，退出码: ' + code);
    }
  });
}

// ── Register custom 'local' scheme for safe local file loading ──
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'local',
    privileges: { bypassCSP: true, stream: true, supportFetchAPI: true },
  },
]);

// ── App Lifecycle ─────────────────────────────────────────

app.whenReady().then(() => {
  // Handle local:// protocol — serves files from disk
  protocol.handle('local', (request) => {
    const filePath = decodeURIComponent(
      request.url.replace('local:///', '')
    ).replace(/^\/+/, '');
    // On Windows, the path after local:///drive/path needs drive letter:
    // local:///C:/Users/... → C:/Users/...
    const fullPath = path.isAbsolute(filePath)
      ? filePath
      : path.resolve(filePath);
    return net.fetch('file:///' + fullPath.replace(/\\/g, '/'));
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
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
