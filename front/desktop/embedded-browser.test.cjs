const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { test } = require('node:test')
const { createEmbeddedBrowser, allowedNavigation } = require('./embedded-browser.cjs')

function fixture() {
  let account = 'erp/first'
  const keys = []
  const windows = []
  const views = []
  class View {
    constructor({ webPreferences }) {
      assert.equal(webPreferences.nodeIntegration, false)
      assert.equal(webPreferences.sandbox, true)
      const contents = new EventEmitter()
      this.webContents = Object.assign(contents, {
        loadURL: async url => { contents.url = url }, getURL: () => contents.url, getTitle: () => '',
        setWindowOpenHandler: handler => { contents.openWindow = handler }, focus() {},
        isDestroyed: () => !!contents.closed, close: () => { contents.closed = true },
      })
      views.push(this)
    }
    setBounds() {}
  }
  class Window extends EventEmitter {
    constructor() { super(); windows.push(this); this.contentView = { addChildView() {} } }
    getContentSize() { return [1100, 800] }
    isVisible() { return !!this.visible }
    show() { this.visible = true }
    hide() { this.visible = false }
    isDestroyed() { return !!this.closed }
    destroy() { this.closed = true; this.emit('closed') }
    setTitle() {}
  }
  const browser = createEmbeddedBrowser({ BaseWindow: Window, WebContentsView: View, parent: () => undefined,
    account: () => account, session: { fromPartition(key) {
      keys.push(key)
      return { flushStorageData() {}, cookies: { async flushStore() {} }, setPermissionRequestHandler() {}, setPermissionCheckHandler() {}, async setProxy() {} }
    } } })
  const call = (action, data, id = 'request') => browser.handle(action, data, account, { id, active: () => true })
  return { browser, windows, views, call, keys, switchAccount(value) { account = value; browser.reset() } }
}

const product = { kind: '1688', url: 'https://detail.1688.com/offer/1.html' }
const canvas = { kind: 'gemini', url: 'https://gemini.google.com/share/owned', profile: 'canvas' }

test('持久化分区按 ERP 账号、服务及会话名隔离，重开沿用同一分区', async () => {
  const { browser, call, keys, switchAccount } = fixture()
  await call('open', product)
  browser.reset()
  await call('open', product)
  await call('open', canvas)
  await call('open', { ...canvas, profile: 'second' })
  switchAccount('erp/second')
  await call('open', product)
  assert.equal(keys[0], keys[1])
  assert.equal(new Set(keys).size, 4)
  assert.ok(keys.every(key => key.startsWith('persist:')))
})

test('游客验证保留原页；取消、手动关闭及任务完成释放对应页面', async () => {
  const { browser, call, windows, views } = fixture()
  const page = await call('open', product)
  assert.equal(windows.length, 0)
  const contents = views[0].webContents
  assert.equal(contents.openWindow({ url: product.url }).action, 'deny')
  await call('show', { page })
  assert.ok(windows[0].isVisible())
  assert.equal(contents.openWindow({ url: 'https://login.taobao.com/' }).action, 'allow')
  assert.equal(contents.openWindow({ url: 'https://other.test' }).action, 'deny')
  browser.finish('request', page)
  assert.equal((await call('list', {})).length, 1)
  await call('hide', { page })
  assert.equal(windows[0].isVisible(), false)
  await call('resume', { page }, 'resume')
  browser.finish('resume')
  assert.ok(contents.isDestroyed())
  const retry = await call('open', product)
  await call('show', { page: retry })
  windows[1].destroy()
  await assert.rejects(call('show', { page: retry }), /已关闭/)
})

test('主页面和跳转限制在对应服务的网页', () => {
  assert.ok(allowedNavigation('1688', 'https://login.taobao.com/member/login.jhtml'))
  assert.ok(allowedNavigation('gemini', 'https://accounts.google.com/'))
  for (const url of ['file:///etc/hosts', 'https://1688.com.other.test', 'https://user@1688.com',
    'http://127.0.0.1:19222', 'javascript:void(0)']) assert.equal(allowedNavigation('1688', url), false)
})

test('登录跳到 1688 首页后只返回一次，继续读取原商品', async () => {
  const { call, views } = fixture()
  const page = await call('open', product)
  await call('show', { page })
  const contents = views[0].webContents
  contents.emit('did-navigate', {}, 'https://login.1688.com/login')
  contents.url = 'https://www.1688.com/'
  assert.equal(await call('return_to_product', { page }), true)
  assert.equal(contents.getURL(), product.url)
  contents.url = 'https://www.1688.com/'
  assert.equal(await call('return_to_product', { page }), false)
})
