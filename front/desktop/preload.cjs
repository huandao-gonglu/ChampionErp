const { contextBridge, ipcRenderer } = require('electron')

// 只暴露固定目录入口，页面不能选择路径或发送任意 IPC。
contextBridge.exposeInMainWorld('championDesktop', {
  localOperations: true,
  configureModels: (models) => ipcRenderer.invoke('desktop:configure-models', models),
  openLicenses: () => ipcRenderer.invoke('licenses:open'),
})
