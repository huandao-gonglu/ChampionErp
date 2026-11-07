const { contextBridge, ipcRenderer } = require('electron')

// 只暴露固定能力，页面不能选择本机路径或发送任意 IPC。
contextBridge.exposeInMainWorld('championDesktop', {
  localOperations: true,
  savedLogin: () => ipcRenderer.invoke('account:saved-login'),
  rememberLogin: options => ipcRenderer.invoke('account:remember-login', options),
  onConnectionChanged: (callback) => {
    const listener = () => callback()
    ipcRenderer.on('desktop:connection-changed', listener)
    return () => ipcRenderer.removeListener('desktop:connection-changed', listener)
  },
  openAlibabaAuthorization: url => ipcRenderer.invoke('desktop:open-alibaba-authorization', url),
  openNewAPI: (target) => ipcRenderer.invoke('account:open-newapi', target),
  configureModels: (models) => ipcRenderer.invoke('desktop:configure-models', models),
  openLicenses: () => ipcRenderer.invoke('licenses:open'),
})
