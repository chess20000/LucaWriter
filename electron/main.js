const { app, BrowserWindow, Menu, dialog, ipcMain } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');

let pyProc = null;
let mainWindow = null;
const PORT = 20000;

function getUserDataPath() {
  return path.join(app.getPath('userData'), 'data');
}

function getBackendExePath() {
  return path.join(process.resourcesPath, 'backend', 'LucaWriterBackend.exe');
}

function getFrontendPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'frontend');
  }
  return path.join(__dirname, '..', 'frontend');
}

function getBuiltinPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'builtin');
  }
  return path.join(__dirname, '..', 'builtin');
}

function startBackend() {
  const dataDir = getUserDataPath();
  fs.mkdirSync(dataDir, { recursive: true });

  const env = Object.assign({}, process.env, {
    DATA_DIR: dataDir,
    FRONTEND_DIR: getFrontendPath(),
    BUILTIN_BOOKS_DIR: getBuiltinPath(),
    LOCAL_LLM_DIR: app.isPackaged ? path.join(process.resourcesPath, 'local_llm') : path.join(__dirname, '..', 'local_llm'),
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

function killBackend() {
  if (pyProc) {
    try {
      if (process.platform === 'win32') {
        spawn('taskkill', ['/PID', String(pyProc.pid), '/T', '/F']);
      } else {
        pyProc.kill('SIGTERM');
      }
    } catch (e) {
      console.error('Error killing backend:', e);
    }
    pyProc = null;
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

ipcMain.on('window-minimize', function() {
  if (mainWindow) mainWindow.minimize();
});

ipcMain.on('window-maximize', function() {
  if (mainWindow) {
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow.maximize();
    }
  }
});

ipcMain.on('window-close', function() {
  if (mainWindow) mainWindow.close();
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
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      devTools: false,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'loading.html'));
  mainWindow.show();

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

  mainWindow.on('closed', function() {
    mainWindow = null;
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
      mainWindow.focus();
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
    loadApp();
  });

  app.on('window-all-closed', function() {
    killBackend();
    app.quit();
  });

  app.on('before-quit', function() {
    killBackend();
  });
}
