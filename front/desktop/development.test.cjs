const assert = require('node:assert/strict')
const { test } = require('node:test')
const { EventEmitter } = require('node:events')
const http = require('node:http')
const { createDevelopmentDesktop, desktopDevelopment, proxyDevelopmentFrontend } = require('./development.cjs')
const { parseDesktopLink, DEV_DESKTOP_SCHEME } = require('./deep-links.cjs')

test('开发交接复用已启动进程，关闭窗口后重建，并在服务结束时退出', async () => {
  const children = []
  const options = []
  const backendUrl = 'http://127.0.0.1:5550'
  const desktop = createDevelopmentDesktop({ root: '/workspace/front', frontendUrl: 'http://127.0.0.1:3300', backendUrl,
    spawnProcess(executable, args, config) {
      options.push({ executable, args, config })
      const child = new EventEmitter()
      child.connected = true
      child.messages = []
      child.kill = () => child.emit('exit', 0)
      child.send = (message, callback) => {
        child.messages.push(message)
        callback?.()
        if (message.type === 'desktop-shutdown') queueMicrotask(() => child.emit('exit', 0))
      }
      children.push(child)
      queueMicrotask(() => child.emit('message', { type: 'desktop-ready' }))
      return child
    },
  })
  await desktop.start()
  await desktop.open('a'.repeat(43))
  await desktop.open('b'.repeat(43))
  assert.equal(children.length, 1)
  assert.equal(desktop.ready, true)
  assert.deepEqual(options[0].args, ['/workspace/front'])
  assert.equal(options[0].config.env.CHAMPION_ERP_BACKEND_URL, backendUrl)
  assert.equal(options[0].config.env.ELECTRON_RUN_AS_NODE, undefined)
  assert.equal(parseDesktopLink(children[0].messages[0].url, backendUrl, DEV_DESKTOP_SCHEME), 'a'.repeat(43))
  children[0].emit('exit', 0)
  assert.equal(desktop.ready, false)
  await desktop.open('c'.repeat(43))
  assert.equal(children.length, 2)
  await desktop.stop()
  assert.equal(children[1].messages.at(-1).type, 'desktop-shutdown')
  assert.equal(desktop.ready, false)
})

test('开发唤起端点拒绝外站、错误主机、非 JSON 和任意链接输入', async () => {
  let handler
  const plugin = desktopDevelopment({ enabled: true, backendUrl: 'http://127.0.0.1:5550' })
  plugin.configureServer({ config: { root: '/workspace/front', server: { port: 3300 } },
    middlewares: { use(value) { handler = value } }, httpServer: new EventEmitter() })
  for (const invalid of [
    { origin: 'https://other.example' },
    { host: 'other.example:3300' },
    { 'content-type': 'text/plain' },
    { origin: undefined },
  ]) {
    let status
    await handler({ url: '/__dev/desktop-handoff', method: 'POST', headers: {
      host: '127.0.0.1:3300', origin: 'http://127.0.0.1:3300', 'content-type': 'application/json', ...invalid,
    } }, { writeHead(value) { status = value }, end() {} })
    assert.equal(status, 403)
  }
  let status
  await handler({ url: '/__dev/desktop-handoff', method: 'POST', headers: {
    host: '127.0.0.1:3300', origin: 'http://127.0.0.1:3300', 'content-type': 'application/json',
  }, async *[Symbol.asyncIterator]() { yield JSON.stringify({ request_id: 'a'.repeat(43), url: 'champion-erp://continue' }) } },
  { writeHead(value) { status = value }, end() {} })
  assert.equal(status, 400)
})

test('桌面开发页面资源转发到 Vite，窗口凭据保留在桌面代理内', async () => {
  let headers
  const vite = http.createServer((request, response) => {
    headers = request.headers
    response.setHeader('Content-Type', 'text/javascript')
    response.end('开发热更新模块')
  })
  await new Promise(resolve => vite.listen(0, '127.0.0.1', resolve))
  const desktop = http.createServer((request, response) => {
    proxyDevelopmentFrontend(request, response, `http://127.0.0.1:${vite.address().port}`)
  })
  await new Promise(resolve => desktop.listen(0, '127.0.0.1', resolve))
  try {
    const response = await fetch(`http://127.0.0.1:${desktop.address().port}/@vite/client`, {
      headers: { Cookie: 'session=test', Authorization: 'secret', 'X-ERP-Local-Window': 'window-secret' },
    })
    assert.equal(await response.text(), '开发热更新模块')
    assert.equal(headers.cookie, undefined)
    assert.equal(headers.authorization, undefined)
    assert.equal(headers['x-erp-local-window'], undefined)
  } finally {
    await Promise.all([new Promise(resolve => desktop.close(resolve)), new Promise(resolve => vite.close(resolve))])
  }
})
