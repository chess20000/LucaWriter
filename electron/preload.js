const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  platform: process.platform,
  minimize: function() { ipcRenderer.send('window-minimize'); },
  maximize: function() { ipcRenderer.send('window-maximize'); },
  close: function() { ipcRenderer.send('window-close'); },
});
