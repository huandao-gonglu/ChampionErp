// 浏览器仅由本机执行器通过私有管道操作，不向网页开放 IPC、调试端口或 ERP 登录凭据。
const { createHash, randomUUID } = require('node:crypto')

const { openLoginEntry } = require('./verification/login-entry.cjs')
const { panelExpression } = require('./verification/login-panel.cjs')

const domains = {
  '1688': ['1688.com', 'taobao.com', 'tmall.com', 'alibaba.com', 'alipay.com'],
  gemini: ['google.com'],
}

function allowedNavigation(kind, address) {
  try {
    const url = new URL(address)
    return ['https:', ...(kind === '1688' ? ['http:'] : [])].includes(url.protocol) &&
      !url.username && !url.password && !url.port &&
      domains[kind].some(host => url.hostname === host || url.hostname.endsWith('.' + host))
  } catch { return false }
}

function createEmbeddedBrowser({ BaseWindow, WebContentsView, session, parent, account, layoutVerificationWindow }) {
  let generation = 0
  const pages = new Map()
  const stores = new Set()
  const commands = new Set(['Runtime.enable', 'Page.enable', 'Runtime.evaluate', 'Page.getFrameTree',
    'Target.setAutoAttach'])
  function close(page) {
    if (!pages.delete(page.id)) return
    for (const popup of page.popups) if (!popup.isDestroyed()) popup.destroy()
    if (page.window && !page.window.isDestroyed()) page.window.destroy()
    if (!page.contents.isDestroyed()) page.contents.close()
  }

  function protect(contents, page) {
    function check(event) {
      if (!allowedNavigation(page.kind, event.url)) event.preventDefault()
    }
    let visitedLogin = false
    contents.on('did-navigate', (_event, address) => {
      if (page.kind === '1688' && /^(login|passport)\./.test(new URL(address).hostname)) {
        visitedLogin = true
        page.visitedLogin = true
      }
    })
    contents.on('will-navigate', check)
    contents.on('will-redirect', event => {
      if (event.isMainFrame) check(event)
    })
    if (page.kind === '1688' && contents !== page.contents)
      contents.once('destroyed', () => { if (visitedLogin) page.loginPopupClosed = true })
    contents.setWindowOpenHandler(({ url }) => {
      if (!page.window?.isVisible() || !allowedNavigation(page.kind, url)) return { action: 'deny' }
      return { action: 'allow', overrideBrowserWindowOptions: { parent: page.window,
        webPreferences: { session: page.store, sandbox: true, nodeIntegration: false, contextIsolation: true } } }
    })
    contents.on('did-create-window', popup => {
      page.popups.add(popup)
      protect(popup.webContents, page)
      popup.once('closed', () => page.popups.delete(popup))
    })
  }

  async function navigate(page, url) {
    const contents = page.contents
    let domReady
    const interactive = new Promise(resolve => { domReady = resolve })
    contents.once('dom-ready', domReady)
    try {
      // 人工验证出现时，页面内的资源仍可能在等待，业务层继续检查原文档。
      await Promise.race([interactive, contents.loadURL(url)])
    } catch (error) {
      const redirected = (error.code === 'ERR_ABORTED' || error.errno === -3) &&
        pages.has(page.id) && (contents.isLoadingMainFrame() || allowedNavigation(page.kind, contents.getURL()))
      if (!redirected) throw error
    } finally {
      contents.removeListener('dom-ready', domReady)
    }
  }

  async function open(data, owner, call) {
    const { kind, url, profile = '', proxy = '' } = data
    if (!domains[kind] || !allowedNavigation(kind, url)) throw new Error('此网址不属于指定的内置浏览服务。')
    const key = createHash('sha256').update(JSON.stringify([owner, kind, profile])).digest('hex')
    const store = session.fromPartition('persist:erp-browser-' + key)
    stores.add(store)
    store.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
    store.setPermissionCheckHandler(() => false)
    await store.setProxy(proxy ? { mode: 'fixed_servers', proxyRules: proxy } : { mode: 'system' })
    if (!call.active() || account() !== owner) throw new Error('ERP 登录会话已切换。')
    const view = new WebContentsView({ webPreferences: { session: store, sandbox: true,
      contextIsolation: true, nodeIntegration: false, backgroundThrottling: false } })
    view.setBounds({ x: 0, y: 0, width: 1100, height: 800 })
    const contents = view.webContents
    const page = { owner, kind, url, callId: call.id, id: randomUUID(), contents, store, view, events: [], popups: new Set() }
    pages.set(page.id, page)
    contents.once('destroyed', () => close(page))
    protect(contents, page)
    try {
      await navigate(page, url)
      return page.id
    } catch (error) {
      close(page)
      throw error
    }
  }

  async function readAsset(page, data) {
    const url = new URL(data.url)
    const limit = data.limit
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
        !Number.isSafeInteger(limit) || limit <= 0 || limit > 20 * 1024 * 1024)
      throw new Error('采集资源地址或大小无效。')
    const response = await page.store.fetch(url.href, {
      referrer: page.contents.getURL(), credentials: 'include',
      signal: AbortSignal.timeout(30000),
    })
    if (!response.ok || !response.body) throw new Error(`采集资源请求失败（HTTP ${response.status}）。`)
    let size = 0
    const chunks = []
    for await (const chunk of response.body) {
      size += chunk.length
      if (size > limit) throw new Error('采集资源超过大小上限。')
      chunks.push(chunk)
    }
    return { data: Buffer.concat(chunks).toString('base64'), mime: response.headers.get('content-type')?.split(';')[0] || '' }
  }

  function show(page) {
    if (!page.window) {
      const win = new BaseWindow({ parent: parent(), width: 1100, height: 800, show: false,
        title: page.kind === 'gemini' ? 'Gemini 登录与验证' : '1688 登录与验证' })
      page.window = win
      win.contentView.addChildView(page.view)
      function resize() {
        const [width, height] = win.getContentSize()
        page.view.setBounds({ x: 0, y: 0, width, height })
      }
      if (layoutVerificationWindow && page.kind === '1688') layoutVerificationWindow(win, page.view)
      else {
        resize()
        win.on('resize', resize)
      }
      win.on('closed', () => { page.window = null; close(page) })
      page.contents.on('did-navigate', (_event, url) => {
        if (!win.isDestroyed()) win.setTitle('登录与验证 · ' + new URL(url).origin)
      })
      win.setTitle('登录与验证 · ' + new URL(page.contents.getURL() || page.url).origin)
    }
    if (!page.window.isVisible()) { page.window.show(); page.view.webContents.focus() }
  }

  async function handle(action, data, owner, call) {
    if (!owner || account() !== owner) throw new Error('ERP 登录会话已失效。')
    if (action === 'open') return open(data, owner, call)
    if (action === 'list') return [...pages.values()].filter(p => p.owner === owner && p.kind === '1688')
      .map(p => ({ id: p.id, source_url: p.url, type: 'page', url: p.contents.getURL(), title: p.contents.getTitle(),
        login_popup_closed: !!p.loginPopupClosed, visited_login: !!p.visitedLogin && !p.returnedFromHome }))
    const page = pages.get(data.page)
    if (!page || page.owner !== owner || page.contents.isDestroyed()) throw new Error('原验证页面已关闭，请重新开始。')
    if (action === 'resume' && page.kind === '1688') {
      page.callId = call.id
      return
    }
    if (action === 'show') {
      show(page)
      if (page.kind === '1688' && data.reason === 'LOGIN' && !page.loginEntryHandled && !page.popups.size) {
        const product = new URL(page.url)
        if (product.origin === 'https://detail.1688.com' && /^\/offer\/\d+\.html$/.test(product.pathname)) {
          // 并发轮询共用一次点击；异步加载的入口留到下次检查，手动关闭后不再弹出。
          page.loginEntryHandled = true
          const expression = `(${openLoginEntry})(${JSON.stringify(product.origin + product.pathname)}, ${panelExpression}.box)`
          const result = await page.contents.executeJavaScript(expression, true).catch(() => 'interrupted')
          if (result === 'waiting') page.loginEntryHandled = false
        }
      }
      return
    }
    if (action === 'hide') {
      for (const popup of page.popups) popup.hide()
      page.window?.hide()
      await persist(page.store)
      return
    }
    if (action === 'return_to_product' && page.kind === '1688') {
      const current = new URL(page.contents.getURL())
      const product = new URL(page.url)
      const landing = ['www.1688.com', '1688.com', 'm.1688.com'].includes(current.hostname) && current.pathname === '/'
      const staleProduct = data.reason === 'LOGIN' && page.loginPopupClosed &&
        product.origin === current.origin && product.pathname === current.pathname
      if (!staleProduct && !(landing && page.visitedLogin && !page.returnedFromHome)) return false
      if (landing) page.returnedFromHome = true
      page.loginPopupClosed = false
      await navigate(page, page.url)
      return true
    }
    if (action === 'asset' && page.kind === '1688') return readAsset(page, data)
    if (action === 'close') { close(page); return }
    if (action === 'navigate' && allowedNavigation(page.kind, data.url)) return navigate(page, data.url)
    if (action !== 'command' || !commands.has(data.method)) throw new Error('不支持的内置网页操作。')
    const control = page.contents.debugger
    if (!control.isAttached()) {
      control.attach('1.3')
      control.on('message', (_event, method, params, sessionId) => {
        if (page.kind === 'gemini' && ['Target.attachedToTarget', 'Runtime.executionContextCreated'].includes(method))
          page.events.push({ method, params, sessionId })
      })
    }
    const result = await control.sendCommand(data.method, data.params, data.session_id || undefined)
    return { result, events: page.events.splice(0) }
  }

  function reset() {
    generation++
    for (const page of pages.values()) close(page)
  }
  function persist(store) {
    store.flushStorageData()
    return store.cookies.flushStore()
  }

  async function flush() {
    reset()
    await Promise.all([...stores].map(persist))
  }
  function finish(callId, keepId) {
    for (const page of pages.values()) {
      if (page.callId !== callId) continue
      if (page.id === keepId && page.kind === '1688' && page.window && !page.window.isDestroyed()) page.callId = null
      else close(page)
    }
  }
  return { handle, finish, reset, flush, get generation() { return generation } }
}

module.exports = { createEmbeddedBrowser, allowedNavigation }
