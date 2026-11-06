const { contextBridge, ipcRenderer } = require('electron')

// 只暴露固定能力，页面不能选择本机路径或发送任意 IPC。
contextBridge.exposeInMainWorld('championDesktop', {
  localOperations: true,
  openNewAPI: (target) => ipcRenderer.invoke('account:open-newapi', target),
  configureModels: (models) => ipcRenderer.invoke('desktop:configure-models', models),
  openLicenses: () => ipcRenderer.invoke('licenses:open'),
})
