const { contextBridge, ipcRenderer } = require('electron')
contextBridge.exposeInMainWorld('preview', {
  start: url => ipcRenderer.invoke('preview:start', url),
  toggle: () => ipcRenderer.invoke('preview:toggle'),
  onStatus: fn => ipcRenderer.on('preview:status', (_event, value) => fn(value)),
})
