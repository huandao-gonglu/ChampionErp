const assert = require('node:assert/strict')
const { test } = require('node:test')
const { registerLocalConfig } = require('./local-config.cjs')

function subject() {
  let handler
  const frame = { url: 'http://127.0.0.1:12345/settings' }
  const win = { webContents: { mainFrame: frame }, once() {} }
  const calls = []
  registerLocalConfig({ ipcMain: { handle(name, value) { assert.equal(name, 'desktop:configure-models'); handler = value }, removeHandler() {} },
    win, frontendUrl: frame.url, runtime: { async perform(call) { calls.push(call); return { ok: true, result: { fingerprints: { cli: 'fingerprint' } } } } } })
  return { handler, calls, event: { sender: win.webContents, senderFrame: frame } }
}

test('本机配置只接受应用主页面及固定字段', async () => {
  const { handler, calls, event } = subject()
  const result = await handler(event, { cli: { command: '/usr/local/bin/codex' } })
  assert.equal(result.fingerprints.cli, 'fingerprint')
  assert.equal(calls[0].operation, 'config.write')
  await assert.rejects(handler({ ...event, sender: {} }, { cli: {} }), /主页面/)
  await assert.rejects(handler(event, { cli: { script: 'untrusted' } }), /未知字段/)
  await assert.rejects(handler(event, { cli: { command: {} } }), /无效值/)
  assert.equal(calls.length, 1)
})
