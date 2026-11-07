const http = require('node:http')
const https = require('node:https')

// 凭据只留在主进程内存；不交给 localhost 的浏览器 Cookie jar。
class AccountSession {
  constructor() { this.cookie = ''; this.csrf = ''; this.email = ''; this.revision = 0 }
  headers() { return { ...(this.cookie ? { Cookie: this.cookie } : {}), ...(this.csrf ? { 'X-CSRF-Token': this.csrf } : {}) } }
  clear() { this.cookie = ''; this.csrf = ''; this.email = ''; this.revision++ }
  accept(headers, body) {
    const cookies = headers['set-cookie'] || []
    const session = cookies.map(value => value.split(';', 1)[0]).find(value => value.startsWith('champion_session='))
    if (!session || !session.slice('champion_session='.length) || !body.user || !body.csrf_token) throw new Error('登录会话响应无效。')
    this.cookie = session
    this.email = body.user.email || ''
    this.csrf = body.csrf_token
    this.revision++
  }
}

function proxyBackendRequest(req, res, baseUrl, { session, client = '', connectionId = () => '', onLogin = async () => {}, onLogout = () => {} }) {
  const requestPath = new URL(req.url || '/', 'http://desktop.invalid')
  const target = new URL(baseUrl)
  target.pathname = requestPath.pathname
  target.search = requestPath.search
  const revision = session.revision
  const headers = { ...req.headers, host: target.host }
  const isLogin = req.method === 'POST' && requestPath.pathname === '/api/account/login'
  // 主进程需要解析登录回执；其余响应仍由渲染页面协商压缩。
  if (isLogin) headers['accept-encoding'] = 'identity'
  delete headers.cookie
  delete headers.authorization
  delete headers['x-real-ip']
  delete headers['x-forwarded-for']
  delete headers['x-champion-client']
  if (client) headers['x-champion-client'] = client
  delete headers['x-champion-desktop']
  if (session.cookie) headers.cookie = session.cookie
  if (!requestPath.pathname.startsWith('/api/account/') && !requestPath.pathname.startsWith('/api/admin/') && connectionId()) headers['x-champion-desktop'] = connectionId()
  if (headers.origin) headers.origin = target.origin
  delete headers['sec-fetch-site']
  const upstream = (target.protocol === 'https:' ? https : http).request(target, { method: req.method, headers }, response => {
    const forwarded = { ...response.headers }
    delete forwarded['set-cookie']
    const status = response.statusCode || 502
    if (status === 401 && revision === session.revision) { session.clear(); onLogout() }
    if (req.method === 'POST' && ['/api/account/logout', '/api/account/password'].includes(requestPath.pathname) && status === 200) {
      session.clear(); onLogout(requestPath.pathname)
    }
    if (isLogin && status === 200) {
      let raw = ''
      response.setEncoding('utf8')
      response.on('data', chunk => { raw += chunk; if (raw.length > 65536) response.destroy(new Error('登录响应超过大小上限。')) })
      response.on('error', fail)
      response.on('end', async () => {
        let acceptedRevision
        try {
          session.accept(response.headers, JSON.parse(raw))
          acceptedRevision = session.revision
          await onLogin()
          res.writeHead(status, forwarded)
          res.end(raw)
        } catch (error) {
          if (acceptedRevision === session.revision) { session.clear(); onLogout() }
          fail(error)
        }
      })
    } else {
      res.writeHead(status, forwarded)
      response.pipe(res)
      response.on('error', error => res.destroy(error))
    }
  })
  function fail(error) {
    if (res.headersSent || res.destroyed) { res.destroy(error); return }
    const upgrade = error?.code === 'DESKTOP_UPDATE_REQUIRED'
    if (upgrade) { session.clear(); onLogout() }
    const raw = JSON.stringify({ ok: false, error: upgrade ? error.message : '业务后端连接失败，请重试。' })
    res.writeHead(upgrade ? 426 : 502, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(raw) })
    res.end(raw)
  }
  upstream.on('error', fail)
  req.on('aborted', () => upstream.destroy())
  res.on('close', () => { if (!res.writableEnded) upstream.destroy() })
  req.pipe(upstream)
}

module.exports = { AccountSession, proxyBackendRequest }
