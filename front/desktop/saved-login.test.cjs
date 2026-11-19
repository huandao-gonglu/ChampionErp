const { test } = require('node:test')
const assert = require('node:assert/strict')
const { mkdtempSync, readFileSync, rmSync } = require('node:fs')
const { tmpdir } = require('node:os')
const path = require('node:path')
const { EventEmitter } = require('node:events')
const { SavedLogin, registerSavedLogin } = require('./saved-login.cjs')

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

test('开发版默认自动登录，保存和关闭该选项后均可在新进程恢复选择', t => {
  const directory = mkdtempSync(path.join(tmpdir(), 'erp-login-preferences-'))
  t.after(() => rmSync(directory, { recursive: true, force: true }))
  const storage = { isEncryptionAvailable: () => true, getSelectedStorageBackend: () => 'gnome_libsecret',
    encryptString: value => Buffer.from(value), decryptString: bytes => bytes.toString() }
  function open(defaultAuto, session = {}) {
    const vault = new SavedLogin(directory, 'http://127.0.0.1:5050', storage)
    const handlers = new Map()
    const frame = { url: 'http://127.0.0.1:43210/login' }
    const win = new EventEmitter()
    win.webContents = { mainFrame: frame }
    registerSavedLogin({ ipcMain: { handle: (key, value) => handlers.set(key, value), removeHandler: key => handlers.delete(key) },
      win, url: 'http://127.0.0.1:43210/', vault, session, defaultAuto })
    const event = { sender: win.webContents, senderFrame: frame }
    return { vault, read: () => handlers.get('account:saved-login')(event),
      save: options => handlers.get('account:remember-login')(event, options) }
  }
  assert.equal(open(false).read().auto, false)
  assert.equal(open(true).read().auto, true)
  const session = { email: 'dev@example.test', cookie: 'session-value', csrf: 'csrf-value' }
  open(true, session).save({ email: session.email, password: '测试密码', remember: false, auto: true })
  const reopened = open(true)
  assert.equal(reopened.vault.read().cookie, session.cookie)
  assert.equal(reopened.vault.read().csrf, session.csrf)
  assert.equal(reopened.vault.read().password, '')
  assert.equal(reopened.read().auto, true)
  assert.equal(reopened.read().cookie, undefined)
  open(true, session).save({ email: session.email, password: '测试密码', remember: false, auto: false })
  assert.equal(open(true).read().auto, false)
  assert.equal(open(true).read().email, session.email)
  assert.equal(open(true).vault.read().cookie, undefined)
  open(true).vault.forgetSession()
  assert.equal(open(true).read().auto, false)
  assert.equal(open(true).vault.read().cookie, undefined)
  open(true).vault.forgetSession(true)
  assert.deepEqual(open(true).vault.read(), {})
})
