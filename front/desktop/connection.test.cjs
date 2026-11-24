const assert = require('node:assert/strict')
const http = require('node:http')
const { test } = require('node:test')
const { setTimeout: delay } = require('node:timers/promises')
const { createAccountConnection, requestJson } = require('./connection.cjs')

async function setup(t, { runtime = { cancel() {}, perform: async () => ({ id: '', ok: true, result: {} }) }, connectOptions = {} } = {}) {
  let generation = 0, valid = '', rejectRegistration = false, resultFailures = 0
  const polls = new Map(), handoffs = [], disconnects = [], results = [], connectionHeaders = []
  const auth = { headers: () => ({ Cookie: 'champion_session=test', 'X-CSRF-Token': 'test' }) }
  const server = http.createServer((req, res) => {
    assert.equal(req.headers.cookie, 'champion_session=test')
    connectionHeaders.push(req.headers.connection)
    const id = req.headers['x-champion-desktop']
    let raw = ''
    req.on('data', chunk => { raw += chunk })
    req.on('end', () => {
      const respond = (status, data) => { res.writeHead(status); res.end(JSON.stringify(data)) }
      if (req.url === '/api/desktop/connect') {
        ++generation
        if (rejectRegistration) { respond(401, { ok: false, error: '请重新登录' }); return }
        valid = String(generation).padStart(40, '0')
        respond(200, { ok: true, connection_id: valid })
      } else if (req.url === '/api/desktop/disconnect') {
        disconnects.push(id)
        if (id === valid) valid = ''
        respond(200, { ok: true })
      } else if (id !== valid) {
        respond(409, { ok: false, error_code: 'DESKTOP_UNAVAILABLE', error: '本机连接不可用' })
      } else if (req.url === '/api/desktop/poll') {
        polls.set(id, res)
      } else if (req.url === '/api/desktop/result') {
        if (resultFailures > 0) {
          resultFailures--
          res.writeHead(522, { 'Content-Type': 'text/html' })
          res.end('<html>Cloudflare 522</html>')
          return
        }
        results.push(JSON.parse(raw || '{}'))
        respond(200, { ok: true, accepted: true })
      } else {
        if (req.url === '/api/operation-availability') assert.equal(req.method, 'GET')
        if (req.url === '/api/desktop-handoff/resolve') handoffs.push(id)
        respond(200, { ok: true })
      }
    })
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const base = 'http://127.0.0.1:' + server.address().port
  const link = createAccountConnection(base, runtime, { session: auth, onChange() {}, connectOptions })
  t.after(async () => { await link.stop(); server.closeAllConnections(); server.close() })
  return {
    link, base, auth, polls, handoffs, disconnects, results, connectionHeaders,
    get registrations() { return generation },
    expire() { valid = '' },
    refuseLogin() { rejectRegistration = true },
    failNextResults(count) { resultFailures = count },
  }
}

test('领取采集交接前同时检查过期连接，只重新注册一次，再用新连接领取', async t => {
  const f = await setup(t)
  await f.link.ensure()
  const first = f.link.id
  await f.link.ensure(true)
  assert.equal(f.registrations, 1)
  f.expire()
  await Promise.all([f.link.ensure(true), f.link.ensure(true)])
  assert.notEqual(f.link.id, first)
  await requestJson(f.base, '/api/desktop-handoff/resolve', { request_id: 'test' }, f.link.id, 1000, undefined, f.auth)
  assert.deepEqual(f.handoffs, [f.link.id])
  assert.equal(f.registrations, 2)
  assert.ok(f.connectionHeaders.every(header => header === 'close'))
})

test('单次轮询闪断在原连接重试，不取消在途任务也不发 /disconnect；服务端失效后自动重连', { timeout: 8000 }, async t => {
  const cancelled = []
  let finishTask
  const started = new Promise(resolve => { finishTask = resolve })
  const runtime = {
    cancel(id) { cancelled.push(id) },
    perform(call) {
      finishTask()
      return delay(80).then(() => ({ id: call.id, ok: true, result: { ok: true } }))
    },
  }
  const f = await setup(t, { runtime, connectOptions: { pollRetryDelaysMs: [20, 20], pollRetryBudgetMs: 500, resultRetryDelaysMs: [20, 20] } })
  await f.link.ensure()
  const id = f.link.id
  while (!f.polls.has(id)) await delay(10)
  const firstPoll = f.polls.get(id)
  f.polls.delete(id)
  firstPoll.writeHead(200)
  firstPoll.end(JSON.stringify({ ok: true, calls: [{ id: 'canvas-1', operation: 'canvas.edit', params: {}, timeout: 30 }] }))
  await started
  // 下一次 poll 瞬时断链：不应取消在途任务，也不应重建连接或发 /disconnect。
  while (!f.polls.has(id)) await delay(10)
  const dropped = f.polls.get(id)
  f.polls.delete(id)
  dropped.destroy()
  while (!f.results.length) await delay(15)
  assert.equal(f.link.id, id)
  assert.equal(f.registrations, 1)
  assert.deepEqual(cancelled, [])
  assert.deepEqual(f.disconnects, [])
  // 服务端明确过期（409 DESKTOP_UNAVAILABLE）时自动重新注册。
  f.expire()
  while (!f.polls.has(id)) await delay(10)
  f.polls.get(id).writeHead(409)
  f.polls.get(id).end(JSON.stringify({ ok: false, error_code: 'DESKTOP_UNAVAILABLE', error: '本机连接不可用' }))
  while (!f.link.id || f.link.id === id) await delay(25)
  assert.equal(f.registrations, 2)
  assert.deepEqual(f.disconnects, [])
  await f.link.stop()
  await assert.rejects(f.link.ensure(), /已结束/)
})

test('大体积图片回执上传遇到 522/闪断时幂等重试，不丢图不断连', { timeout: 8000 }, async t => {
  const runtime = {
    cancel() { assert.fail('回执重试不应取消任务') },
    async perform(call) {
      return { id: call.id, ok: true, result: { images: [{ data: 'ZmFrZQ==', mime: 'image/png' }] } }
    },
  }
  const f = await setup(t, { runtime, connectOptions: { resultRetryDelaysMs: [15, 15, 15] } })
  f.failNextResults(2)
  await f.link.ensure()
  const id = f.link.id
  while (!f.polls.has(id)) await delay(10)
  const poll = f.polls.get(id)
  f.polls.delete(id)
  poll.writeHead(200)
  poll.end(JSON.stringify({ ok: true, calls: [{ id: 'img-1', operation: 'canvas.edit', params: {}, timeout: 30 }] }))
  while (!f.results.length) await delay(15)
  assert.equal(f.results[0].id, 'img-1')
  assert.equal(f.results[0].ok, true)
  assert.equal(f.link.id, id)
  assert.equal(f.registrations, 1)
  assert.deepEqual(f.disconnects, [])
})

test('登录已失效时停止后台重试', async t => {
  const f = await setup(t)
  f.refuseLogin()
  await assert.rejects(f.link.ensure(), { status: 401 })
  await delay(1200)
  assert.equal(f.registrations, 1)
})
