const { test } = require('node:test')
const assert = require('node:assert/strict')
const { mkdtempSync, readFileSync, rmSync } = require('node:fs')
const { tmpdir } = require('node:os')
const path = require('node:path')
const { SavedLogin } = require('./saved-login.cjs')

test('重开应用分别恢复密码和会话，隔离站点，退出和改密清除相应凭据', t => {
  const directory = mkdtempSync(path.join(tmpdir(), 'erp-vault-'))
  t.after(() => rmSync(directory, { recursive: true, force: true }))
  const storage = { isEncryptionAvailable: () => true, getSelectedStorageBackend: () => 'gnome_libsecret',
    encryptString: value => Buffer.from(Buffer.from(value).toString('base64')),
    decryptString: bytes => Buffer.from(bytes.toString(), 'base64').toString() }
  const open = (site = 'https://erp.example') => new SavedLogin(directory, site, storage)
  for (const password of ['', '测试密码']) for (const cookie of ['', '测试会话']) {
    open().write({ password, cookie })
    assert.equal(open().read().password || '', password)
    assert.equal(open().read().cookie || '', cookie)
    assert.deepEqual(open('https://other.example').read(), {})
  }
  assert.equal(readFileSync(open().file).includes(Buffer.from('测试密码')), false)
  open().forgetSession()
  assert.equal(open().read().password, '测试密码')
  assert.equal(open().read().cookie, undefined)
  open().forgetSession(true)
  assert.deepEqual(open().read(), {})
})
