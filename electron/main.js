const { app, BrowserWindow, BrowserView, Menu, dialog, ipcMain, Tray, nativeImage } = require('electron');
const { spawn } = require('child_process');
const crypto = require('crypto');
const path = require('path');
const fs = require('fs');
const http = require('http');

let pyProc = null;
let mainWindow = null;
let browserCtrlWindow = null;
let browserTabs = {};       // { tabId: { view: BrowserView, title: str, url: str, cdpTargetId: str } }
let browserActiveTab = null;
let browserTabSeq = 0;
const BROWSER_CTRL_PORT = 9224;
let browserCtrlToken = '';
let tray = null;
let isKilling = false;
let isQuitting = false;
const PORT = 20000;
const BROWSER_DEBUG_PORT = 9223;

// 为浏览器控制功能启用 CDP 调试端口
app.commandLine.appendSwitch('remote-debugging-port', String(BROWSER_DEBUG_PORT));
app.commandLine.appendSwitch('remote-allow-origins', '*');

function getUserDataPath() {
  return path.join(app.getPath('userData'), 'data');
}

function getBackendExePath() {
  var exeName = process.platform === 'win32' ? 'LucaWriterBackend.exe' : 'LucaWriterBackend';
  return path.join(process.resourcesPath, 'backend', exeName);
}

function getFrontendPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'frontend');
  }
  return path.join(__dirname, '..', 'frontend');
}

function getWindowIconPath() {
  return path.join(__dirname, process.platform === 'win32' ? 'icon.ico' : 'icon.png');
}

function getBuiltinPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'builtin');
  }
  return path.join(__dirname, '..', 'builtin');
}

function getSettingsPath() {
  return path.join(getUserDataPath(), 'settings.json');
}

function readKeepBackground() {
  try {
    if (!fs.existsSync(getSettingsPath())) return false;
    var raw = fs.readFileSync(getSettingsPath(), 'utf-8');
    var s = JSON.parse(raw);
    return !!s.keep_background;
  } catch (e) {
    return false;
  }
}

function readAccessScope() {
  try {
    if (!fs.existsSync(getSettingsPath())) return '127.0.0.1';
    var raw = fs.readFileSync(getSettingsPath(), 'utf-8');
    var s = JSON.parse(raw);
    return s.access_scope || '127.0.0.1';
  } catch (e) {
    return '127.0.0.1';
  }
}

function startBackend() {
  const dataDir = getUserDataPath();
  fs.mkdirSync(dataDir, { recursive: true });

  browserCtrlToken = crypto.randomBytes(32).toString('hex');

  const env = Object.assign({}, process.env, {
    DATA_DIR: dataDir,
    FRONTEND_DIR: getFrontendPath(),
    BUILTIN_BOOKS_DIR: getBuiltinPath(),
    LOCAL_LLM_DIR: app.isPackaged ? path.join(process.resourcesPath, 'local_llm') : path.join(__dirname, '..', 'local_llm'),
    BROWSER_DEBUG_PORT: String(BROWSER_DEBUG_PORT),
    BROWSER_CTRL_PORT: String(BROWSER_CTRL_PORT),
    BROWSER_CTRL_TOKEN: browserCtrlToken,
  });

  if (app.isPackaged) {
    const exePath = getBackendExePath();
    if (!fs.existsSync(exePath)) {
      console.error('Backend exe not found:', exePath);
      return false;
    }
    pyProc = spawn(exePath, [], { env: env, stdio: 'pipe' });
  } else {
    const mainPy = path.join(__dirname, '..', 'backend', 'main.py');
    pyProc = spawn('python', [mainPy], { env: env, stdio: 'pipe' });
  }

  pyProc.stdout.on('data', function(data) {
    console.log('[Backend] ' + data.toString().trim());
  });

  pyProc.stderr.on('data', function(data) {
    console.error('[Backend] ' + data.toString().trim());
  });

  pyProc.on('close', function(code) {
    console.log('Backend exited with code ' + code);
    pyProc = null;
  });

  pyProc.on('error', function(err) {
    console.error('Failed to start backend:', err);
    pyProc = null;
  });

  return true;
}

async function killBackend() {
  if (isKilling) {
    console.log('Backend is already being killed, skipping duplicate call');
    return;
  }

  if (!pyProc) {
    console.log('No backend process to kill');
    return;
  }

  isKilling = true;
  const pid = pyProc.pid;
  let killPromise;

  try {
    if (process.platform === 'win32') {
      killPromise = new Promise((resolve, reject) => {
        const taskkill = spawn('taskkill', ['/PID', String(pid), '/T', '/F'], {
          stdio: 'ignore'
        });

        taskkill.on('close', (code) => {
          if (code === 0 || code === 128) {
            console.log(`Backend process ${pid} terminated successfully`);
            resolve();
          } else {
            console.warn(`Taskkill exited with code ${code}, process may still be running`);
            resolve();
          }
        });

        taskkill.on('error', (err) => {
          console.error('Taskkill error:', err);
          resolve();
        });
      });
    } else {
      pyProc.kill('SIGTERM');
      killPromise = Promise.resolve();
    }

    await Promise.race([
      killPromise,
      new Promise(resolve => setTimeout(resolve, 3000))
    ]);

  } catch (e) {
    console.error('Error killing backend:', e);
  } finally {
    pyProc = null;
    setTimeout(() => {
      isKilling = false;
    }, 1000);
  }
}

function waitForBackend(maxRetries, interval) {
  maxRetries = maxRetries || 60;
  interval = interval || 1000;
  return new Promise(function(resolve, reject) {
    var retries = 0;
    function check() {
      http.get('http://localhost:' + PORT + '/login.html', function(res) {
        resolve();
      }).on('error', function() {
        retries++;
        if (retries >= maxRetries) {
          reject(new Error('Backend failed to start within ' + Math.round(maxRetries * interval / 1000) + 's'));
        } else {
          setTimeout(check, interval);
        }
      });
    }
    check();
  });
}

function createTray() {
  if (tray) return;

  var iconPath = path.join(__dirname, 'tray-icon.png');
  if (!fs.existsSync(iconPath)) {
    iconPath = path.join(getFrontendPath(), 'tray-icon.png');
  }
  var trayIcon;
  try {
    trayIcon = nativeImage.createFromPath(iconPath);
    if (trayIcon.isEmpty()) {
      trayIcon = nativeImage.createFromPath(path.join(__dirname, 'icon.ico'));
    }
    if (trayIcon.isEmpty()) {
      trayIcon = nativeImage.createFromPath(path.join(getFrontendPath(), 'icon.ico'));
    }
  } catch (e) {
    trayIcon = nativeImage.createEmpty();
  }

  tray = new Tray(trayIcon);
  tray.setToolTip('LucaWriter');

  updateTrayMenu();

  tray.on('double-click', function() {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
      if (!readKeepBackground()) destroyTray();
    }
  });
}

function updateTrayMenu() {
  if (!tray) return;

  var scope = readAccessScope();
  var scopeLabel = scope === '0.0.0.0' ? '0.0.0.0（全部）' : '127.0.0.1（仅本机）';

  var contextMenu = Menu.buildFromTemplate([
    { label: 'LucaWriter', enabled: false },
    { label: '监听: ' + scopeLabel, enabled: false },
    { type: 'separator' },
    { label: '退出', click: function() { isQuitting = true; destroyTray(); app.quit(); }},
  ]);

  tray.setContextMenu(contextMenu);
}

function destroyTray() {
  if (tray) {
    tray.destroy();
    tray = null;
  }
}

function isMainWindowZoomed() {
  if (!mainWindow) return false;
  return process.platform === 'darwin' ? mainWindow.isFullScreen() : mainWindow.isMaximized();
}

function notifyWindowZoomState() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('maximize-change', isMainWindowZoomed());
  }
}

ipcMain.on('window-minimize', function() {
  if (mainWindow) mainWindow.minimize();
});

ipcMain.on('window-maximize', function() {
  if (mainWindow) {
    if (process.platform === 'darwin') {
      mainWindow.setFullScreen(!mainWindow.isFullScreen());
    } else if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow.maximize();
    }
  }
});

ipcMain.on('window-close', function() {
  if (!mainWindow) return;
  if (readKeepBackground() && (process.platform === 'win32' || process.platform === 'darwin')) {
    mainWindow.hide();
    if (process.platform === 'win32') createTray();
  } else {
    isQuitting = true;
    mainWindow.close();
  }
});

ipcMain.handle('window-is-maximized', function() {
  return isMainWindowZoomed();
});

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    show: false,
    title: 'LucaWriter',
    backgroundColor: '#111111',
    frame: false,
    icon: getWindowIconPath(),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      devTools: false,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'loading.html'));
  mainWindow.maximize();
  mainWindow.show();

  mainWindow.on('maximize', notifyWindowZoomState);
  mainWindow.on('unmaximize', notifyWindowZoomState);
  mainWindow.on('enter-full-screen', notifyWindowZoomState);
  mainWindow.on('leave-full-screen', notifyWindowZoomState);

  mainWindow.on('close', function(event) {
    if (!isQuitting && readKeepBackground() && (process.platform === 'win32' || process.platform === 'darwin')) {
      event.preventDefault();
      mainWindow.hide();
      if (process.platform === 'win32') createTray();
    }
  });

  mainWindow.on('closed', function() {
    mainWindow = null;
    destroyTray();
  });

  Menu.setApplicationMenu(null);

  mainWindow.webContents.on('before-input-event', function(event, input) {
    if (input.key === 'F12') {
      event.preventDefault();
    }
    if (input.control && input.shift && (input.key === 'I' || input.key === 'i')) {
      event.preventDefault();
    }
    if (input.control && input.shift && (input.key === 'J' || input.key === 'j')) {
      event.preventDefault();
    }
    if (input.control && (input.key === 'U' || input.key === 'u')) {
      event.preventDefault();
    }
  });
}

async function loadApp() {
  try {
    await waitForBackend();
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL('http://localhost:' + PORT);
    }
  } catch (err) {
    console.error('Failed to start backend:', err);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(
        '<html><body style="background:#111;color:#e8e4de;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0">' +
        '<div style="text-align:center"><h1>LucaWriter \u542F\u52A8\u5931\u8D25</h1>' +
        '<p style="color:#8a8580;margin-top:12px">' + err.message + '</p></div></body></html>'
      ));
    }
  }
}

var gotTheLock = app.requestSingleInstanceLock();

if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', function() {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      if (!mainWindow.isVisible()) mainWindow.show();
      mainWindow.focus();
      destroyTray();
    }
  });

  app.on('ready', function() {
    var started = startBackend();
    if (!started) {
      dialog.showErrorBox('\u542F\u52A8\u5931\u8D25', '\u627E\u4E0D\u5230\u540E\u7AEF\u7A0B\u5E8F\uFF0C\u8BF7\u91CD\u65B0\u5B89\u88C5 LucaWriter\u3002');
      app.quit();
      return;
    }
    createWindow();
    startBrowserCtrlServer();
    loadApp();

function ensureBrowserCtrlWindow() {
  if (browserCtrlWindow && !browserCtrlWindow.isDestroyed()) return;

  browserCtrlWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    show: false,
    parent: mainWindow,
    skipTaskbar: true,
    title: 'LucaWriter 浏览器',
    backgroundColor: '#1a1a2e',
    icon: getWindowIconPath(),
    webPreferences: {
      preload: path.join(__dirname, 'browser-preload.js'),
      devTools: false,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      webviewTag: true,
    },
  });

  var browserHtml = path.join(__dirname, 'browser.html');
  if (!fs.existsSync(browserHtml)) {
    browserHtml = path.join(getFrontendPath(), 'browser.html');
  }
  if (!fs.existsSync(browserHtml)) {
    browserHtml = path.join(__dirname, '..', 'electron', 'browser.html');
  }

  browserCtrlWindow.loadFile(browserHtml);

  browserCtrlWindow.on('resize', updateActiveBrowserViewBounds);
  browserCtrlWindow.on('closed', function() {
    browserCtrlWindow = null;
    Object.keys(browserTabs).forEach(function(id) {
      var t = browserTabs[id];
      if (t.view) { try { t.view.webContents.destroy(); } catch(e) {} }
    });
    browserTabs = {};
    browserActiveTab = null;
  });

  createBrowserTab('新标签页', 'about:blank');
}

function updateActiveBrowserViewBounds() {
  if (!browserCtrlWindow || browserCtrlWindow.isDestroyed()) return;
  var tb = browserCtrlWindow.getBounds();
  var tabBarHeight = 36;
  var contentBounds = { x: 0, y: tabBarHeight, width: tb.width, height: tb.height - tabBarHeight };
  Object.keys(browserTabs).forEach(function(id) {
    var t = browserTabs[id];
    if (t.view) {
      if (id === browserActiveTab) {
        t.view.setBounds(contentBounds);
      } else {
        t.view.setBounds({ x: -10000, y: -10000, width: 1, height: 1 });
      }
    }
  });
}

function createBrowserTab(title, url) {
  browserTabSeq++;
  var tabId = 'tab_' + browserTabSeq;
  var view = new BrowserView({
    webPreferences: {
      devTools: false,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  browserCtrlWindow.addBrowserView(view);
  view.webContents.loadURL(url || 'about:blank');

  browserTabs[tabId] = { view: view, title: title, url: url || 'about:blank', cdpTargetId: '' };

  view.webContents.on('page-title-updated', function(e, t) {
    if (browserTabs[tabId]) {
      browserTabs[tabId].title = t || title;
      browserTabs[tabId].url = view.webContents.getURL();
      if (browserCtrlWindow && !browserCtrlWindow.isDestroyed()) {
        browserCtrlWindow.webContents.send('browser-tab-updated', { tabId: tabId, title: t });
      }
    }
  });

  view.webContents.on('did-navigate', function(e, u) {
    if (browserTabs[tabId]) {
      browserTabs[tabId].url = u;
    }
  });

  view.webContents.on('destroyed', function() {
    if (browserTabs[tabId]) {
      delete browserTabs[tabId];
    }
  });

  if (browserCtrlWindow && !browserCtrlWindow.isDestroyed()) {
    browserCtrlWindow.webContents.send('browser-tab-created', { tabId: tabId, title: title });
  }

  switchBrowserTab(tabId);
  return tabId;
}

function switchBrowserTab(tabId) {
  if (!browserTabs[tabId]) return;
  browserActiveTab = tabId;
  updateActiveBrowserViewBounds();
  if (browserCtrlWindow && !browserCtrlWindow.isDestroyed()) {
    browserCtrlWindow.webContents.send('browser-switched', { tabId: tabId });
  }
}

function closeBrowserTab(tabId) {
  if (!browserTabs[tabId]) return;
  var t = browserTabs[tabId];
  try { t.view.webContents.destroy(); } catch(e) {}
  browserCtrlWindow.removeBrowserView(t.view);
  delete browserTabs[tabId];

  if (browserCtrlWindow && !browserCtrlWindow.isDestroyed()) {
    browserCtrlWindow.webContents.send('browser-tab-removed', { tabId: tabId });
  }

  if (browserActiveTab === tabId) {
    var ids = Object.keys(browserTabs);
    if (ids.length > 0) {
      switchBrowserTab(ids[ids.length - 1]);
    } else {
      browserActiveTab = null;
    }
  }
}

// IPC: 标签页操作
ipcMain.on('browser-new-tab', function() {
  createBrowserTab('新标签页', 'about:blank');
});

ipcMain.on('browser-close-tab', function(e, tabId) {
  closeBrowserTab(tabId);
});

ipcMain.on('browser-switch-tab', function(e, tabId) {
  switchBrowserTab(tabId);
});

// 内部 HTTP 服务器：Python 后端通过此接口获取活跃标签的 CDP 信息
function startBrowserCtrlServer() {
  http.createServer(function(req, res) {
    res.setHeader('Content-Type', 'application/json');

    if (req.method === 'OPTIONS') {
      res.writeHead(200); res.end(); return;
    }

    if (browserCtrlToken && req.headers['x-ctrl-token'] !== browserCtrlToken) {
      res.writeHead(403); res.end(JSON.stringify({ ok: false, error: 'unauthorized' })); return;
    }

    var url = req.url;
    var match;

    if (url === '/tab/active') {
      if (!browserActiveTab || !browserTabs[browserActiveTab]) {
        res.writeHead(200); res.end(JSON.stringify({ ok: false, error: 'no active tab' })); return;
      }
      var t = browserTabs[browserActiveTab];
      var wcId = t.view.webContents.id;
      res.writeHead(200);
      res.end(JSON.stringify({
        ok: true,
        tabId: browserActiveTab,
        webContentsId: wcId,
        url: t.url,
        title: t.title,
        debugPort: BROWSER_DEBUG_PORT,
      }));
      return;
    }

    if (url === '/tab/new') {
      ensureBrowserCtrlWindow();
      var tabId = createBrowserTab('新标签页', 'about:blank');
      var nt = browserTabs[tabId];
      res.writeHead(200);
      res.end(JSON.stringify({
        ok: true,
        tabId: tabId,
        webContentsId: nt.view.webContents.id,
        debugPort: BROWSER_DEBUG_PORT,
      }));
      return;
    }

    if (req.method === 'POST' && (match = url.match(/^\/tab\/navigate\/(.+)$/))) {
      var tid = match[1];
      if (!browserTabs[tid]) {
        res.writeHead(404); res.end(JSON.stringify({ ok: false, error: 'tab not found' })); return;
      }
      var body = '';
      req.on('data', function(c) { body += c; });
      req.on('end', function() {
        try {
          var d = JSON.parse(body);
          browserTabs[tid].view.webContents.loadURL(d.url);
          browserTabs[tid].url = d.url;
          res.writeHead(200);
          res.end(JSON.stringify({ ok: true }));
        } catch(e) {
          res.writeHead(400);
          res.end(JSON.stringify({ ok: false, error: String(e) }));
        }
      });
      return;
    }

    res.writeHead(404);
    res.end(JSON.stringify({ ok: false, error: 'not found' }));
  }).listen(BROWSER_CTRL_PORT, '127.0.0.1', function() {
    console.log('Browser control API on http://127.0.0.1:' + BROWSER_CTRL_PORT);
  });
}
  });

  app.on('window-all-closed', function() {
    if (process.platform !== 'darwin') {
      if (!readKeepBackground() || isQuitting) {
        app.quit();
      }
    }
  });

  app.on('activate', function() {
    if (process.platform === 'darwin') {
      if (!mainWindow) {
        createWindow();
        loadApp();
      } else {
        mainWindow.show();
        mainWindow.focus();
      }
    }
  });

  app.on('before-quit', async function(event) {
    if (!isQuitting && readKeepBackground() && process.platform === 'win32') {
      return;
    }
    event.preventDefault();
    destroyTray();
    if (browserCtrlWindow && !browserCtrlWindow.isDestroyed()) {
      browserCtrlWindow.destroy();
    }
    try {
      await killBackend();
    } catch (err) {
      console.error('Error during backend cleanup:', err);
    } finally {
      app.exit(0);
    }
  });
}
