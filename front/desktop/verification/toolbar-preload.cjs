const { ipcRenderer } = require('electron')
window.addEventListener('DOMContentLoaded', () => {
  const button = document.querySelector('button')
  button.onclick = () => ipcRenderer.send('verification:toggle')
  ipcRenderer.on('verification:status', (_event, { label, full }) => {
    document.querySelector('strong').textContent = label
    button.textContent = full ? '自动裁剪' : '显示完整页面'
  })
})
