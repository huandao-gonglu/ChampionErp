const { normalizeBackendUrl } = require('./connection.cjs')

const DESKTOP_SCHEME = 'champion-erp'
const DEV_DESKTOP_SCHEME = 'champion-erp-dev'

function parseDesktopLink(value, backendUrl, scheme = DESKTOP_SCHEME) {
  if (typeof value !== 'string' || value.length > 4096) throw new Error('桌面链接无效。')
  const url = new URL(value)
  if (url.protocol !== `${scheme}:` || url.hostname !== 'continue' || url.port || url.username || url.password
      || !['', '/'].includes(url.pathname) || url.hash
      || [...url.searchParams.keys()].some(key => !['request', 'origin'].includes(key))
      || url.searchParams.getAll('request').length !== 1 || url.searchParams.getAll('origin').length !== 1) {
    throw new Error('桌面链接无效。')
  }
  if (normalizeBackendUrl(url.searchParams.get('origin')) !== normalizeBackendUrl(backendUrl)) {
    throw new Error('此链接来自其他 ERP 站点，请使用连接该站点的客户端。')
  }
  const request = url.searchParams.get('request')
  if (!/^[\w-]{43}$/.test(request)) throw new Error('桌面打开请求无效。')
  return request
}

function desktopLinkArgument(argv, scheme = DESKTOP_SCHEME) {
  return argv.find(value => typeof value === 'string' && value.toLowerCase().startsWith(`${scheme}:`))
}

function registerDesktopHandoff({ ipcMain, win, frontendUrl, pending, finish }) {
  function trusted(event) {
    if (event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame
        || new URL(event.senderFrame.url).origin !== new URL(frontendUrl).origin) {
      throw new Error('仅应用主页面可以接收桌面打开请求。')
    }
  }
  ipcMain.handle('desktop:pending-handoff', event => { trusted(event); return pending() })
  ipcMain.handle('desktop:finish-handoff', (event, id) => { trusted(event); finish(id) })
  win.once('closed', () => {
    ipcMain.removeHandler('desktop:pending-handoff')
    ipcMain.removeHandler('desktop:finish-handoff')
  })
}

module.exports = { DESKTOP_SCHEME, DEV_DESKTOP_SCHEME, parseDesktopLink, desktopLinkArgument, registerDesktopHandoff }
