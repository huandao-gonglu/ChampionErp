const { randomBytes } = require('node:crypto')
const { requestJson } = require('./connection.cjs')

// 系统浏览器通过本机一次性页面 POST 交接，不共享桌面的 Cookie。
class NewAPIBrowserBridge {
  constructor({ backendBaseUrl, frontendUrl, session }) {
    this.backendBaseUrl = backendBaseUrl
    this.origin = new URL(frontendUrl).origin
    this.session = session
    this.pending = new Map()
  }

  async prepare(target) {
    if (!['keys', 'wallet', 'usage'].includes(target) || !this.session.cookie) throw new Error('请先登录后重试。')
    const revision = this.session.revision
    const launch = await requestJson(this.backendBaseUrl, '/api/account/newapi/launch', { target, source_origin: this.origin }, '', 10000, undefined, this.session)
    if (revision !== this.session.revision || !this.session.cookie) throw new Error('账号会话已变更，请重试。')
    const destination = new URL(launch.url)
    const local = destination.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(destination.hostname)
    if ((destination.protocol !== 'https:' && !local) || destination.username || destination.password || destination.search || destination.hash || destination.pathname !== '/api/erp/login' || !/^[A-Za-z0-9_-]{32,128}$/.test(launch.ticket)) throw new Error('登录交接响应无效。')
    for (const [id, value] of this.pending) if (value.expires <= Date.now() || value.revision !== revision) this.pending.delete(id)
    if (this.pending.size >= 8) throw new Error('请稍后重新打开。')
    const nonce = randomBytes(32).toString('base64url')
    this.pending.set(nonce, { url: destination.href, ticket: launch.ticket, revision, expires: Date.now() + 60000 })
    return `${this.origin}/__desktop/newapi/${nonce}`
  }

  handle(req, res) {
    const url = new URL(req.url || '/', this.origin)
    if (!url.pathname.startsWith('/__desktop/newapi/')) return false
    const nonce = url.pathname.slice('/__desktop/newapi/'.length)
    const launch = this.pending.get(nonce)
    const valid = req.method === 'GET' && req.headers.host === new URL(this.origin).host && !url.search && launch && launch.revision === this.session.revision && this.session.cookie && launch.expires > Date.now()
    if (valid) this.pending.delete(nonce)
    res.setHeader('Cache-Control', 'no-store')
    res.setHeader('Referrer-Policy', 'origin')
    res.setHeader('X-Content-Type-Options', 'nosniff')
    if (!valid) { res.writeHead(410, { 'Content-Type': 'text/plain; charset=utf-8' }); res.end('登录交接已过期，请回到 ERP 重新打开。'); return true }
    const scriptNonce = randomBytes(24).toString('base64url')
    const escape = value => value.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    res.setHeader('Content-Security-Policy', `default-src 'none'; script-src 'nonce-${scriptNonce}'; form-action ${new URL(launch.url).origin}; base-uri 'none'; frame-ancestors 'none'`)
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
    res.end(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>正在打开 Token 与账单</title></head><body><form method="post" action="${escape(launch.url)}"><input type="hidden" name="ticket" value="${launch.ticket}"></form><script nonce="${scriptNonce}">document.forms[0].submit()</script></body></html>`)
    return true
  }
}

function registerNewAPIBridge({ ipcMain, win, shell, bridge }) {
  ipcMain.handle('account:open-newapi', async (event, target) => {
    const frame = event.senderFrame
    if (event.sender !== win.webContents || !frame || frame !== win.webContents.mainFrame || new URL(frame.url).origin !== bridge.origin) throw new Error('仅应用主页面可以打开 Token 与账单。')
    await shell.openExternal(await bridge.prepare(target))
  })
  win.once('closed', () => { ipcMain.removeHandler('account:open-newapi'); bridge.pending.clear() })
}

module.exports = { NewAPIBrowserBridge, registerNewAPIBridge }
