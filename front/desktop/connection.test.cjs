const assert = require('node:assert/strict')
const http = require('node:http')
const { test } = require('node:test')
const { setTimeout: delay } = require('node:timers/promises')
const { createAccountConnection, requestJson } = require('./connection.cjs')

async function setup(t) {
  let generation = 0, valid = '', rejectRegistration = false
  const polls = new Map(), handoffs = []
  const auth = { headers: () => ({ Cookie: 'champion_session=test', 'X-CSRF-Token': 'test' }) }
  const server = http.createServer((req, res) => {
    assert.equal(req.headers.cookie, 'champion_session=test')
    const id = req.headers['x-champion-desktop']
    const respond = (status, data) => { res.writeHead(status); res.end(JSON.stringify(data)) }
    if (req.url === '/api/desktop/connect') {
      ++generation
      if (rejectRegistration) { respond(401, { ok: false, error: '请重新登录' }); return }
      valid = String(generation).padStart(40, '0')
      respond(200, { ok: true, connection_id: valid })
    } else if (id !== valid) {
      respond(409, { ok: false, error_code: 'DESKTOP_UNAVAILABLE', error: '本机连接不可用' })
    } else if (req.url === '/api/desktop/poll') {
      polls.set(id, res)
    } else {
      if (req.url === '/api/operation-availability') assert.equal(req.method, 'GET')
      if (req.url === '/api/desktop-handoff/resolve') handoffs.push(id)
      respond(200, { ok: true })
    }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const base = 'http://127.0.0.1:' + server.address().port
  const link = createAccountConnection(base, { cancel() {} }, { session: auth, onChange() {} })
  t.after(async () => { await link.stop(); server.closeAllConnections(); server.close() })
  return { link, base, auth, polls, handoffs, get registrations() { return generation },
    expire() { valid = '' }, refuseLogin() { rejectRegistration = true } }
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
})

test('轮询断线后自动恢复，退出后停止重连', { timeout: 8000 }, async t => {
  const f = await setup(t)
  await f.link.ensure()
  const id = f.link.id
  while (!f.polls.has(id)) await delay(10)
  f.polls.get(id).destroy()
  while (!f.link.id || f.link.id === id) await delay(25)
  assert.equal(f.registrations, 2)
  await f.link.stop()
  await assert.rejects(f.link.ensure(), /已结束/)
})

test('登录已失效时停止后台重试', async t => {
  const f = await setup(t)
  f.refuseLogin()
  await assert.rejects(f.link.ensure(), { status: 401 })
  await delay(1200)
  assert.equal(f.registrations, 1)
})
