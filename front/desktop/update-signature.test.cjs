const { test } = require('node:test')
const assert = require('node:assert/strict')
const { canUseNativeUpdates, LOCAL_SIGNING_CERTIFICATE } = require('./update-signature.cjs')

test('固定自签身份和 Developer ID 共用原生更新入口，实际执行完整性检查', async () => {
  let call
  assert.equal(await canUseNativeUpdates('/test/ERP.app', { platform: 'darwin', execute: async (...args) => { call = args } }), true)
  assert.equal(call[0], '/usr/bin/codesign')
  assert.deepEqual(call[1].slice(0, 4), ['--verify', '--deep', '--strict', '-R'])
  assert.ok(call[1][4].includes(LOCAL_SIGNING_CERTIFICATE.sha1))
  assert.ok(call[1][4].includes('identifier "com.champion.erp.front"'))
  assert.ok(call[1][4].includes('anchor apple generic'))
  assert.equal(call[1].at(-1), '/test/ERP.app')
})

test('临时签名、其他证书、内容损坏或工具错误均不能启用原生更新', async () => {
  for (const message of ['临时签名', '证书不符', '签名损坏', '无法读取应用']) {
    assert.equal(await canUseNativeUpdates('/test/ERP.app', { platform: 'darwin', execute: async () => { throw new Error(message) } }), false)
  }
  assert.equal(await canUseNativeUpdates('/test/ERP.app', { platform: 'win32', execute: async () => { assert.fail('其他平台不调用 Mac 工具') } }), false)
})
