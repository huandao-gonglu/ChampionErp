const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { test } = require('node:test')
const { createEmbeddedBrowser, allowedNavigation, CANVAS_IDLE_MS } = require('./embedded-browser.cjs')

function fixture(development = false) {
  let account = 'erp/first'
  const keys = []
  const windows = []
  const views = []
  const networkErrors = []
  const clearedPartitions = []
  const partitionCookies = new Map()
  const userAgents = new Map()
  const permissionChecks = new Map()
  class View {
    constructor({ webPreferences }) {
      assert.equal(webPreferences.nodeIntegration, false)
      assert.equal(webPreferences.sandbox, true)
      const contents = new EventEmitter()
      this.webContents = Object.assign(contents, {
        id: views.length + 1,
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
    showInactive() { this.visible = true }
    hide() { this.visible = false }
    isDestroyed() { return !!this.closed }
    destroy() { this.closed = true; this.emit('closed') }
    setTitle() {}
  }
  const storesByKey = new Map()
  const browser = createEmbeddedBrowser({ BaseWindow: Window, WebContentsView: View, parent: () => undefined,
    development, account: () => account, session: { fromPartition(key) {
      keys.push(key)
      if (!storesByKey.has(key)) {
        storesByKey.set(key, {
          webRequest: { onErrorOccurred(handler) { networkErrors.push(handler) } },
          flushStorageData() {},
          async clearStorageData() { clearedPartitions.push(key); partitionCookies.set(key, []) },
          async clearCache() {},
          async clearAuthCache() {},
          getUserAgent: () => userAgents.get(key) || 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) gongluErp/1.2.189 Chrome/147.0.0.0 Electron/43.0.0 Safari/537.36',
          setUserAgent(value) { userAgents.set(key, value) },
          cookies: {
            async flushStore() {},
            async get() { return partitionCookies.get(key) || [] },
          },
          setPermissionRequestHandler() {},
          setPermissionCheckHandler(handler) { permissionChecks.set(key, handler) },
          async setProxy() {},
        })
      }
      return storesByKey.get(key)
    } } })
  const call = (action, data, id = 'request') => browser.handle(action, data, account, { id, active: () => true })
  return {
    browser, windows, views, call, keys, networkErrors, clearedPartitions, partitionCookies, userAgents, permissionChecks,
    switchAccount(value) { account = value; browser.reset() },
  }
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
  assert.ok(allowedNavigation('gemini', 'https://accounts.youtube.com/accounts/SetSID?ssdc=1'))
  assert.ok(allowedNavigation('gemini', 'https://accounts.google.com.hk/accounts/SetSID?ssdc=1'))
  assert.ok(allowedNavigation('gemini', 'https://accounts.google.co.jp/accounts/SetSID?ssdc=1'))
  assert.equal(allowedNavigation('gemini', 'https://www.youtube.com/watch?v=123'), false)
  assert.equal(allowedNavigation('gemini', 'https://accounts.google.com.evil.test/'), false)
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

test('1688 等到需要登录且入口可用后只触发一次，验证轮询不会重复点击', async () => {
  const { call, views } = fixture()
  const page = await call('open', product)
  let attempts = 0
  views[0].webContents.executeJavaScript = async () => ++attempts === 1 ? 'waiting' : 'opened'
  await call('show', { page, reason: 'CAPTCHA' })
  assert.equal(attempts, 0)
  await call('show', { page, reason: 'LOGIN' })
  await Promise.all([call('show', { page, reason: 'LOGIN' }), call('show', { page, reason: 'LOGIN' })])
  await call('show', { page, reason: 'LOGIN' })
  assert.equal(attempts, 2)
})

test('Google 登录跳转中止后仅在同会话确认登录成功时恢复原页面', async () => {
  const { call, views } = fixture()
  const page = await call('open', canvas)
  const contents = views[0].webContents
  contents.url = 'https://accounts.google.com/v3/signin/challenge/ipp/verify'
  assert.equal(await call('recover_login', { page, expression: 'auth-check' }), false)
  assert.equal(views.length, 1)
  let prevented = false
  const interrupt = () => contents.emit('will-redirect', { url: 'https://unrelated.test/', isMainFrame: true,
    preventDefault() { prevented = true } })
  interrupt()
  assert.equal(prevented, true)
  // 第一次探测仍需验证，原验证码页保留。
  const pending = call('recover_login', { page, expression: 'auth-check' })
  views[1].webContents.executeJavaScript = async () => ({ signedIn: false })
  assert.equal(await pending, false)
  assert.ok(contents.getURL().includes('/challenge/'))
  assert.ok(views[1].webContents.isDestroyed())
  interrupt()
  const recovered = call('recover_login', { page, expression: 'auth-check' })
  views[2].webContents.executeJavaScript = async () => ({ signedIn: true })
  assert.equal(await recovered, true)
  assert.equal(contents.getURL(), canvas.url)
  assert.ok(views[2].webContents.isDestroyed())
})

test('主文档中止触发登录核对，资源中止和取消的任务不触发恢复跳转', async () => {
  const { browser, call, views, networkErrors } = fixture()
  const page = await call('open', canvas)
  const contents = views[0].webContents
  contents.url = 'https://accounts.google.com/v3/signin/challenge/ipp/verify'
  const error = { webContentsId: contents.id, error: 'net::ERR_ABORTED', resourceType: 'image' }
  networkErrors[0](error)
  assert.equal(await call('recover_login', { page, expression: 'auth-check' }), false)
  networkErrors[0]({ ...error, resourceType: 'mainFrame' })
  const pending = browser.handle('recover_login', { page, expression: 'auth-check' }, 'erp/first', { id: 'request', active: () => false })
  views[1].webContents.executeJavaScript = async () => ({ signedIn: true })
  assert.equal(await pending, false)
  assert.ok(contents.getURL().includes('/challenge/'))
  assert.ok(views[1].webContents.isDestroyed())
})

test('Canvas 预检和修图复用同一预览，失败和账号切换释放页面', async () => {
  const { browser, call, views, windows, switchAccount } = fixture()
  const page = await call('open', canvas, 'check')
  await call('show', { page, reason: 'CANVAS_PREVIEW' }, 'check')
  await call('release_canvas', { page }, 'check')
  browser.finish('check')
  assert.equal(windows[0].isVisible(), false)
  assert.equal(await call('open', canvas, 'edit'), page)
  assert.equal(views.length, 1)
  await assert.rejects(call('show', { page }, 'check'), /其他操作/)
  browser.finish('edit')
  assert.ok(views[0].webContents.isDestroyed())
  const next = await call('open', canvas)
  await call('release_canvas', { page: next })
  switchAccount('erp/second')
  assert.ok(views[1].webContents.isDestroyed())
})

test('普通网页读取保留同时到达的预览事件，交给下次通道检查', async () => {
  const { call, views } = fixture()
  const page = await call('open', canvas)
  const control = new EventEmitter()
  views[0].webContents.debugger = Object.assign(control, {
    isAttached: () => control.attached, attach() { control.attached = true },
    async sendCommand(method) {
      if (method === 'Runtime.evaluate') control.emit('message', {}, 'Target.attachedToTarget', {}, 'preview')
      return {}
    },
  })
  assert.deepEqual((await call('command', { page, method: 'Runtime.evaluate' })).events, [])
  const { events } = await call('command', { page, method: 'Page.getFrameTree', collect_events: true })
  assert.equal(events.length, 1)
  assert.equal(events[0].sessionId, 'preview')
})

test('Canvas 目标或代理变化时新开页面，空闲预览到期释放', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { call, views } = fixture()
  for (const model of [canvas, { ...canvas, url: 'https://gemini.google.com/app/next' },
    { ...canvas, url: 'https://gemini.google.com/app/next', proxy: 'http://localhost:8080' }]) {
    const page = await call('open', model)
    await call('release_canvas', { page })
  }
  assert.equal(views.length, 3)
  assert.ok(views.slice(0, 2).every(view => view.webContents.isDestroyed()))
  t.mock.timers.tick(60000)
  assert.equal(views[2].webContents.isDestroyed(), false)
  t.mock.timers.tick(CANVAS_IDLE_MS)
  assert.ok(views[2].webContents.isDestroyed())
})

test('开发版显示任务原页和弹窗，登录完成和预览交接后仍然可见', async () => {
  const { browser, call, windows, views } = fixture(true)
  const page = await call('open', canvas, 'check')
  assert.ok(windows[0].isVisible())
  const popup = new EventEmitter()
  const popupContents = new EventEmitter()
  Object.assign(popupContents, { setWindowOpenHandler() {} })
  Object.assign(popup, { webContents: popupContents, isDestroyed: () => false,
    hide: () => assert.fail('不应自动隐藏弹窗'), destroy: () => assert.fail('不应提前关闭弹窗') })
  views[0].webContents.emit('did-create-window', popup)
  await call('hide', { page }, 'check')
  await call('release_canvas', { page }, 'check')
  assert.equal(await call('open', canvas, 'edit'), page)
  assert.ok(windows[0].isVisible())
  popup.emit('closed')
  browser.finish('edit')
  assert.ok(views[0].webContents.isDestroyed())
})

test('开发版保留失败的 1688 页面供查看，不会恢复到失败任务', async () => {
  const { browser, call, windows, views } = fixture(true)
  const page = await call('open', product)
  assert.ok(windows[0].isVisible())
  await call('hide', { page })
  browser.finish('request', undefined, true)
  assert.ok(windows[0].isVisible())
  assert.deepEqual(await call('list', {}), [])
  await assert.rejects(call('resume', { page }), /已关闭/)
  await call('open', product, 'next')
  assert.ok(views[0].webContents.isDestroyed())
  assert.ok(windows[1].isVisible())
  browser.reset()
  assert.ok(windows[1].isDestroyed())
})

test('开发版显示 Google 登录核对页，核对结束后原窗口仍可见', async () => {
  const { call, windows, views, networkErrors } = fixture(true)
  const page = await call('open', canvas)
  const contents = views[0].webContents
  contents.url = 'https://accounts.google.com/signin/'
  networkErrors[0]({ webContentsId: contents.id, resourceType: 'mainFrame', error: 'net::ERR_ABORTED' })
  const pending = call('recover_login', { page, expression: 'auth-check' })
  assert.ok(windows[1].isVisible())
  views[1].webContents.executeJavaScript = async () => ({ signedIn: false })
  assert.equal(await pending, false)
  assert.ok(windows[1].isDestroyed())
  assert.ok(windows[0].isVisible())
})

test('Canvas 多账号按 ERP 账号隔离，支持去重、启停与取消登录清理分区', async () => {
  const { createHash } = require('node:crypto')
  const { call, clearedPartitions, partitionCookies, switchAccount } = fixture()
  assert.deepEqual((await call('canvas_accounts_list', {})).accounts, [])
  const pageA = await call('open', { ...canvas, profile: 'acct_a' }, 'login-a')
  const partitionA = 'persist:erp-browser-' + createHash('sha256').update(JSON.stringify(['erp/first', 'gemini', 'acct_a'])).digest('hex')
  const partitionB = 'persist:erp-browser-' + createHash('sha256').update(JSON.stringify(['erp/first', 'gemini', 'acct_b'])).digest('hex')
  partitionCookies.set(partitionA, [{ name: 'SID', value: 'cookie-a' }])
  partitionCookies.set(partitionB, [{ name: 'SID', value: 'cookie-b' }])
  await call('canvas_account_upsert', { profile: 'acct_a', email: 'alice@gmail.com', name: 'Alice', status: 'signed_in' }, 'login-a')
  await assert.rejects(call('canvas_account_logout', { profile: 'acct_a' }, 'logout-busy'), /正在执行修图任务/)
  await call('release_canvas', { page: pageA }, 'login-a')
  await call('canvas_account_upsert', { profile: 'acct_b', email: 'bob@gmail.com', name: 'Bob', status: 'signed_in' }, 'login-b')
  const toggled = await call('canvas_account_set_enabled', { profile: 'acct_b', enabled: false }, 'toggle-b')
  assert.equal(toggled.accounts.find(item => item.profile === 'acct_b').enabled, false)
  const partitionA2 = 'persist:erp-browser-' + createHash('sha256').update(JSON.stringify(['erp/first', 'gemini', 'acct_a2'])).digest('hex')
  partitionCookies.set(partitionA2, [{ name: 'SID', value: 'cookie-a2' }])
  const deduped = await call('canvas_account_upsert', { profile: 'acct_a2', email: 'alice@gmail.com', name: 'Alice', status: 'signed_in' }, 'login-a2')
  assert.deepEqual(deduped.accounts.map(item => item.profile), ['acct_b', 'acct_a2'])
  assert.ok(clearedPartitions.includes(partitionA))
  const afterLogout = await call('canvas_account_logout', { profile: 'acct_a2' }, 'logout-a2')
  assert.deepEqual(afterLogout.accounts.map(item => item.profile), ['acct_b'])
  assert.ok(clearedPartitions.includes(partitionA2))
  switchAccount('erp/second')
  assert.deepEqual((await call('canvas_accounts_list', {})).accounts, [])
})

test('Google 登录页保留原生 UA、不挂载 CDP 调试器，并在新认证 Cookie 落盘后自动恢复跳转', async () => {
  const { createHash } = require('node:crypto')
  const { call, views, userAgents, permissionChecks, partitionCookies } = fixture()
  const page = await call('open', canvas)
  const partition = 'persist:erp-browser-' + createHash('sha256').update(JSON.stringify(['erp/first', 'gemini', 'canvas'])).digest('hex')
  assert.equal(userAgents.has(partition), false)
  const permCheck = permissionChecks.get(partition)
  assert.equal(permCheck(null, 'storage-access', 'https://accounts.google.com'), true)
  assert.equal(permCheck(null, 'storage-access', 'https://evil.test'), false)
  assert.equal(permCheck(null, 'notifications', 'https://accounts.google.com'), false)

  let attached = false
  let detached = false
  views[0].webContents.debugger = Object.assign(new EventEmitter(), {
    isAttached: () => attached,
    attach() { attached = true },
    detach() { attached = false; detached = true },
    async sendCommand() { throw new Error('不应在 Google 登录页发送 CDP 命令') },
  })
  views[0].webContents.url = 'https://accounts.google.com/v3/signin/challenge/pwd'
  const authReply = await call('command', {
    page,
    method: 'Runtime.evaluate',
    params: { expression: 'SignOutOptions loginRequired' },
  })
  assert.equal(attached, false)
  assert.deepEqual(authReply.result.result.value, { signedIn: false, loginRequired: true, email: '', name: '' })

  // 即使未触发 ERR_ABORTED，只要新写入 Google 认证 Cookie，recover_login 会核验并跳转回原 Canvas 页。
  partitionCookies.set(partition, [{ name: '__Secure-1PSID', value: 'fresh-session-token', domain: '.google.com' }])
  const recovering = call('recover_login', { page, expression: 'auth-check' })
  await new Promise(resolve => setImmediate(resolve))
  let probeCalls = 0
  views[1].webContents.executeJavaScript = async () => (++probeCalls === 1
    ? { signedIn: false, loginRequired: false }
    : { signedIn: true, loginRequired: false })
  assert.equal(await recovering, true)
  assert.equal(probeCalls, 2)
  assert.equal(views[0].webContents.getURL(), canvas.url)
  assert.equal(detached, false)
})

test('交互式 Google 登录在网络等待前立即弹出窗口并直接打开 ServiceLogin', async () => {
  const { call, windows, views } = fixture()
  const loginUrl = 'https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fgemini.google.com%2Fshare%2Fowned'
  const page = await call('open', {
    ...canvas,
    initial_url: loginUrl,
    show: true,
  })
  assert.equal(windows.length, 1)
  assert.equal(windows[0].isVisible(), true)
  assert.equal(views[0].webContents.getURL(), loginUrl)
  // 登录恢复后仍导航回原 Canvas 地址而非 initial_url。
  views[0].webContents.emit('will-redirect', {
    url: 'https://unrelated.test/',
    isMainFrame: true,
    preventDefault() {},
  })
  const recovered = call('recover_login', { page, expression: 'auth-check' })
  views[1].webContents.executeJavaScript = async () => ({ signedIn: true })
  assert.equal(await recovered, true)
  assert.equal(views[0].webContents.getURL(), canvas.url)
})
