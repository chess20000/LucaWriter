const { contextBridge, ipcRenderer } = require('electron');

var _originalFetch = window.fetch;
var _patchedFetch = function(url, options) {
  options = options || {};
  options.headers = options.headers || {};
  if (typeof options.headers === 'object' && !(options.headers instanceof Headers)) {
    options.headers['X-Luca-Client'] = 'electron';
  }
  return _originalFetch.call(this, url, options);
};
window.fetch = _patchedFetch;

var _originalXHROpen = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(method, url) {
  this._lucaPatched = true;
  return _originalXHROpen.apply(this, arguments);
};
var _originalXHRSend = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.send = function(body) {
  if (this._lucaPatched) {
    try { this.setRequestHeader('X-Luca-Client', 'electron'); } catch(e) {}
  }
  return _originalXHRSend.call(this, body);
};

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,
  platform: process.platform,
  minimize: function() { ipcRenderer.send('window-minimize'); },
  maximize: function() { ipcRenderer.send('window-maximize'); },
  close: function() { ipcRenderer.send('window-close'); },
  onMaximizeChange: function(callback) { ipcRenderer.on('maximize-change', function(event, isMaximized) { callback(isMaximized); }); },
  isMaximized: function() { return ipcRenderer.invoke('window-is-maximized'); },
});
