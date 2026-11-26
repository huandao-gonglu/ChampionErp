const assert = require('node:assert/strict')
const http = require('node:http')
const { test } = require('node:test')
const { AccountSession, proxyBackendRequest } = require('./account-session.cjs')

async function listen(server) {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return `http://127.0.0.1:${server.address().port}`
}

test('主进程接收 Secure Cookie，渲染页面只取得用户和 CSRF，后续代理覆盖伪造凭据', async t => {
  const received = []
  const backend = http.createServer((req, res) => {
    received.push({ url: req.url, headers: req.headers })
    res.setHeader('Content-Type', 'application/json')
    if (req.url === '/api/account/login') {
      res.setHeader('Set-Cookie', 'champion_session=private-remote-session; HttpOnly; Secure; Path=/; SameSite=Lax')
      const login = JSON.stringify({ ok: true, user: { id: 'a' }, csrf_token: 'csrf-a' })
      if (req.headers['accept-encoding']?.includes('gzip')) {
        res.setHeader('Content-Encoding', 'gzip')
        res.end(require('node:zlib').gzipSync(login))
      } else res.end(login)
    } else res.end(JSON.stringify({ ok: true }))
  })
  const base = await listen(backend)
  const session = new AccountSession()
  let connected = 0
  const proxy = http.createServer((req, res) => proxyBackendRequest(req, res, base, {
    session, connectionId: () => 'trusted-desktop', onLogin: async () => { connected++ },
  }))
  const local = await listen(proxy)
  t.after(() => { proxy.closeAllConnections(); proxy.close(); backend.closeAllConnections(); backend.close() })
  const login = await fetch(local + '/api/account/login', { method: 'POST', headers: { 'Accept-Encoding': 'gzip' }, body: '{}' })
  assert.equal(login.status, 200)
  assert.equal(login.headers.get('set-cookie'), null)
  assert.deepEqual(await login.json(), { ok: true, user: { id: 'a' }, csrf_token: 'csrf-a' })
  assert.equal(connected, 1)
  assert.equal(received[0].headers['accept-encoding'], 'identity')
  assert.deepEqual(session.headers(), { Cookie: 'champion_session=private-remote-session', 'X-CSRF-Token': 'csrf-a' })
  await fetch(local + '/api/state', { headers: { Cookie: 'champion_session=forged', 'X-Champion-Desktop': 'forged', 'X-Real-IP': '198.51.100.2' } })
  assert.equal(received.at(-1).headers.cookie, 'champion_session=private-remote-session')
  assert.equal(received.at(-1).headers['x-champion-desktop'], 'trusted-desktop')
  assert.equal(received.at(-1).headers['x-real-ip'], undefined)
  assert.equal(new AccountSession().headers().Cookie, undefined)
})

test('退出、改密及 401 清除主进程会话并断开本机连接', async t => {
  let logoutConfirmed = false
  const backend = http.createServer((req, res) => {
    res.statusCode = req.url === '/api/state' ? 401 : req.url === '/api/account/logout' && !logoutConfirmed ? 503 : 200
    res.end('{}')
  })
  const base = await listen(backend)
  const session = new AccountSession()
  let disconnected = 0
  const proxy = http.createServer((req, res) => proxyBackendRequest(req, res, base, { session, onLogout: () => { disconnected++ } }))
  const local = await listen(proxy)
  t.after(() => { proxy.closeAllConnections(); proxy.close(); backend.closeAllConnections(); backend.close() })
  session.cookie = 'champion_session=old'
  session.csrf = 'old-csrf'
  const failed = await fetch(local + '/api/account/logout', { method: 'POST' })
  assert.equal(failed.status, 503)
  assert.equal(session.cookie, 'champion_session=old')
  assert.equal(session.csrf, 'old-csrf')
  assert.equal(disconnected, 0)
  logoutConfirmed = true
  for (const path of ['/api/account/logout', '/api/account/password', '/api/state']) {
    session.cookie = 'champion_session=old'
    session.csrf = 'old-csrf'
    await fetch(local + path, { method: path === '/api/state' ? 'GET' : 'POST' })
    assert.equal(session.cookie, '')
    assert.equal(session.csrf, '')
  }
  assert.equal(disconnected, 3)
})

test('旧请求迟到的 401 不清除新登录会话', async t => {
  let reply
  const arrived = new Promise(resolve => { reply = resolve })
  let pending
  const backend = http.createServer((req, res) => { pending = res; reply() })
  const base = await listen(backend)
  const session = new AccountSession()
  session.cookie = 'champion_session=old'
  const proxy = http.createServer((req, res) => proxyBackendRequest(req, res, base, { session }))
  const local = await listen(proxy)
  t.after(() => { proxy.closeAllConnections(); proxy.close(); backend.closeAllConnections(); backend.close() })
  const response = fetch(local + '/api/state')
  await arrived
  session.accept({ 'set-cookie': ['champion_session=new; Secure; HttpOnly'] }, { user: { id: 'b' }, csrf_token: 'new-csrf' })
  pending.writeHead(401); pending.end('{}')
  assert.equal((await response).status, 401)
  assert.equal(session.cookie, 'champion_session=new')
})

test('主进程已有会话但渲染页未持 CSRF 时，登录代理补齐会话 CSRF 且禁用 keep-alive 复用', async t => {
  const received = []
  const backend = http.createServer((req, res) => {
    received.push({ url: req.url, headers: req.headers })
    res.setHeader('Content-Type', 'application/json')
    if (req.url === '/api/account/login') {
      if (req.headers.cookie === 'champion_session=old' && req.headers['x-csrf-token'] !== 'old-csrf') {
        res.statusCode = 403
        res.end(JSON.stringify({ ok: false, error: '请求校验失败，请刷新页面后重试。', error_code: 'CSRF_INVALID' }))
        return
      }
      res.setHeader('Set-Cookie', 'champion_session=fresh; HttpOnly; Secure; Path=/; SameSite=Lax')
      res.end(JSON.stringify({ ok: true, user: { id: 'u1', email: 'u1@example.test' }, csrf_token: 'fresh-csrf' }))
      return
    }
    res.end(JSON.stringify({ ok: true }))
  })
  const base = await listen(backend)
  const session = new AccountSession()
  session.cookie = 'champion_session=old'
  session.csrf = 'old-csrf'
  session.email = 'u1@example.test'
  const proxy = http.createServer((req, res) => proxyBackendRequest(req, res, base, { session }))
  const local = await listen(proxy)
  t.after(() => { proxy.closeAllConnections(); proxy.close(); backend.closeAllConnections(); backend.close() })
  const response = await fetch(local + '/api/account/login', { method: 'POST', body: '{}' })
  assert.equal(response.status, 200)
  assert.equal(received[0].headers.cookie, 'champion_session=old')
  assert.equal(received[0].headers['x-csrf-token'], 'old-csrf')
  assert.equal(received[0].headers.connection, 'close')
  assert.equal(session.cookie, 'champion_session=fresh')
  assert.equal(session.csrf, 'fresh-csrf')
})
