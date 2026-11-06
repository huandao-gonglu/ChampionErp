const { test, afterEach } = require('node:test')
const assert = require('node:assert/strict')
const http = require('node:http')
const { AccountSession } = require('./account-session.cjs')
const { NewAPIBrowserBridge, registerNewAPIBridge } = require('./newapi.cjs')
const cleanups = []
afterEach(async () => { for (const cleanup of cleanups.splice(0)) await cleanup() })
async function setup() {
  const session = new AccountSession()
  session.cookie = 'champion_session=private-main-process-cookie'; session.csrf = 'main-csrf'
  const server = http.createServer(async (req, res) => {
    assert.equal(req.url, '/api/account/newapi/launch')
    assert.equal(req.headers.cookie, session.cookie)
    assert.equal(req.headers['x-csrf-token'], 'main-csrf')
    let raw = ''; for await (const chunk of req) raw += chunk
    assert.equal(JSON.parse(raw).source_origin, bridge.origin)
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ ok: true, url: 'http://localhost:13000/api/erp/login', ticket: 'one-use-ticket-from-erp-with-enough-entropy' }))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  let bridge
  const frontend = http.createServer((req, res) => { if (!bridge.handle(req, res)) { res.writeHead(404); res.end() } })
  await new Promise(resolve => frontend.listen(0, '127.0.0.1', resolve))
  bridge = new NewAPIBrowserBridge({ backendBaseUrl: `http://127.0.0.1:${server.address().port}`, frontendUrl: `http://127.0.0.1:${frontend.address().port}`, session })
  cleanups.push(() => { server.closeAllConnections();frontend.closeAllConnections();return Promise.all([new Promise(r=>server.close(r)),new Promise(r=>frontend.close(r))]) })
  return { bridge, session }
}
test('桌面打开系统浏览器时只传本机一次性入口，自动表单不包含 ERP Cookie', async () => {
  const { bridge } = await setup()
  const url = await bridge.prepare('wallet')
  assert.equal(new URL(url).origin, bridge.origin)
  assert.ok(!url.includes('ticket'))
  const page = await fetch(url)
  const html = await page.text()
  assert.equal(page.status, 200)
  assert.match(html, /method="post" action="http:\/\/localhost:13000\/api\/erp\/login"/)
  assert.match(html, /document.forms\[0\].submit\(\)/)
  assert.ok(!html.includes('private-main-process-cookie'))
  assert.equal(page.headers.get('set-cookie'), null)
  assert.equal(page.headers.get('referrer-policy'), 'origin')
  assert.equal((await fetch(url)).status, 410)
})
test('桌面退出后旧入口失效，页面不能指定任意外部登录地址', async () => {
  const { bridge, session } = await setup()
  await assert.rejects(bridge.prepare('https://attacker.invalid'), /请先登录/)
  const url = await bridge.prepare('keys')
  session.clear()
  assert.equal((await fetch(url)).status, 410)
})
test('只有桌面主页面能够发起账号交接 IPC', async () => {
  const { bridge } = await setup()
  let handler, opened
  const frame = { url: bridge.origin + '/account' }
  const contents = { mainFrame: frame }
  registerNewAPIBridge({ ipcMain: { handle: (_, fn) => { handler = fn } }, win: { webContents: contents, once: () => {} }, shell: { openExternal: async url => { opened = url } }, bridge })
  await assert.rejects(handler({ sender: contents, senderFrame: { url: 'https://attacker.invalid' } }, 'keys'), /仅应用主页面/)
  assert.equal(opened, undefined)
  await handler({ sender: contents, senderFrame: frame }, 'keys')
  assert.ok(opened.startsWith(bridge.origin + '/__desktop/newapi/'))
})
