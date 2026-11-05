function registerLicenseDirectory({ ipcMain, shell, win, frontendUrl, directory }) {
  const origin = new URL(frontendUrl).origin
  ipcMain.handle('licenses:open', async (event) => {
    const frame = event.senderFrame
    if (event.sender !== win.webContents || !frame || frame !== win.webContents.mainFrame || new URL(frame.url).origin !== origin) {
      throw new Error('仅应用主页面可以打开许可证目录。')
    }
    const error = await shell.openPath(directory)
    if (error) throw new Error('无法打开许可证目录。')
  })
  win.once('closed', () => ipcMain.removeHandler('licenses:open'))
}

module.exports = { registerLicenseDirectory }
