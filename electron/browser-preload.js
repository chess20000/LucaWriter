const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('browserAPI', {
  on: function(channel, callback) {
    ipcRenderer.on(channel, function(event, data) { callback(data); });
  },
  send: function(channel, data) {
    ipcRenderer.send(channel, data);
  }
});
