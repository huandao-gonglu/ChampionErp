const { execFile } = require('node:child_process')
const { promisify } = require('node:util')
const path = require('node:path')

const run = promisify(execFile)
const channel = 'desktop:open-alibaba-authorization'
const official = 'https://auth.1688.com'

async function openAlibabaAuthorization(value, { app, shell, platform = process.platform, execute = run }) {
  const url = new URL(value)
  const officialLogin = url.origin === official && url.pathname === '/auth/authorize.htm'
  const oneboundLogin = url.origin === 'https://o0b.cn' && url.pathname === '/9vXze'
    && url.searchParams.size === 1 && !!url.searchParams.get('state')?.trim()
  if ((!officialLogin && !oneboundLogin) || url.username || url.password || url.hash) {
    throw new Error('1688 授权地址无效，请重新授权。')
  }
  try {
    if (platform === 'darwin') {
      const browser = await app.getApplicationInfoForProtocol(url.origin)
      if (path.basename(browser.path) === 'Google Chrome.app') {
        // 单独启动 Chrome，让它按日常资料目录交接窗口，避免系统选中同应用的无头实例。
        await execute('/usr/bin/open', ['-n', '-a', browser.path, '--args', '--new-window', url.href])
        return
      }
    }
    await shell.openExternal(url.href)
  } catch {
    // 系统错误可能包含完整授权地址。
    throw new Error('无法打开系统浏览器，请重新授权。')
  }
}

function registerAlibabaAuthorization({ ipcMain, win, frontendUrl, app, shell }) {
  const origin = new URL(frontendUrl).origin
  ipcMain.handle(channel, (event, value) => {
    const frame = event.senderFrame
    if (event.sender !== win.webContents || !frame || frame !== win.webContents.mainFrame || new URL(frame.url).origin !== origin) {
      throw new Error('仅 ERP 主窗口可以打开 1688 授权。')
    }
    return openAlibabaAuthorization(value, { app, shell })
  })
  win.once('closed', () => ipcMain.removeHandler(channel))
}

module.exports = { registerAlibabaAuthorization, openAlibabaAuthorization }
