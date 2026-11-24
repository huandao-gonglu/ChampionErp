// 浏览器仅由本机执行器通过私有管道操作，不向网页开放 IPC、调试端口或 ERP 登录凭据。
const { createHash, randomUUID } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const { openLoginEntry } = require('./verification/login-entry.cjs')
const { panelExpression } = require('./verification/login-panel.cjs')

const CANVAS_IDLE_MS = 5 * 60 * 1000
const GOOGLE_AUTH_COOKIE_NAMES = new Set(['SID', 'SAPISID', '__Secure-1PSID', '__Secure-3PSID', 'HSID', 'SSID'])
const PROFILE_PATTERN = /^[A-Za-z0-9._-]{1,80}$/

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

function createEmbeddedBrowser({ BaseWindow, WebContentsView, session, parent, account, layoutVerificationWindow, development = false, accountsFile = '', canvasIdleMs = CANVAS_IDLE_MS }) {
  let generation = 0
  const pages = new Map()
  const stores = new Set()
  const memoryRegistry = new Map()
  const commands = new Set(['Runtime.enable', 'Page.enable', 'Runtime.evaluate', 'Page.getFrameTree',
    'Target.setAutoAttach'])

  function ownerHash(owner) {
    return createHash('sha256').update(String(owner)).digest('hex')
  }

  function partitionKey(owner, kind, profile = '') {
    return createHash('sha256').update(JSON.stringify([owner, kind, profile])).digest('hex')
  }

  function readAllRegistries() {
    if (!accountsFile) return Object.fromEntries(memoryRegistry)
    try {
      if (!fs.existsSync(accountsFile)) return {}
      const parsed = JSON.parse(fs.readFileSync(accountsFile, 'utf8'))
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
    } catch { return {} }
  }

  function writeAllRegistries(all) {
    if (!accountsFile) {
      memoryRegistry.clear()
      for (const [k, v] of Object.entries(all)) memoryRegistry.set(k, v)
      return
    }
    fs.mkdirSync(path.dirname(accountsFile), { recursive: true, mode: 0o700 })
    const temporary = `${accountsFile}.${randomUUID()}.tmp`
    fs.writeFileSync(temporary, JSON.stringify(all, null, 2), { mode: 0o600 })
    fs.renameSync(temporary, accountsFile)
  }

  function normalizeAccountRow(raw) {
    const profile = String(raw?.profile || raw?.id || '').trim()
    if (!PROFILE_PATTERN.test(profile)) throw new Error('Google 账号会话标识无效。')
    const status = ['signed_in', 'login_required', 'rate_limited', 'signed_out', 'unknown'].includes(raw?.status)
      ? raw.status : 'unknown'
    return {
      id: profile,
      profile,
      email: String(raw?.email || '').trim().slice(0, 200),
      name: String(raw?.name || '').trim().slice(0, 120),
      enabled: raw?.enabled !== false,
      status,
      last_checked_at: String(raw?.last_checked_at || '').trim().slice(0, 64),
      last_error: String(raw?.last_error || '').trim().slice(0, 500),
    }
  }

  async function hasGoogleAuthCookies(store) {
    if (!store?.cookies?.get) return null
    try {
      const cookies = await store.cookies.get({ domain: 'google.com' })
      if (!Array.isArray(cookies)) return null
      return cookies.some(cookie => GOOGLE_AUTH_COOKIE_NAMES.has(cookie.name) && Boolean(cookie.value))
    } catch { return null }
  }

  function isProfileBusy(owner, profile) {
    const key = partitionKey(owner, 'gemini', profile)
    return [...pages.values()].some(page => page.owner === owner && page.kind === 'gemini' && page.key === key && Boolean(page.callId) && !page.idleTimer)
  }

  async function clearProfilePartition(owner, profile) {
    const key = partitionKey(owner, 'gemini', profile)
    for (const page of [...pages.values()]) {
      if (page.owner === owner && page.kind === 'gemini' && page.key === key) close(page)
    }
    const store = session.fromPartition('persist:erp-browser-' + key)
    if (store?.clearStorageData) await store.clearStorageData()
    if (store?.clearCache) await store.clearCache()
    if (store?.clearAuthCache) await store.clearAuthCache()
    if (store) await persist(store)
  }

  async function listCanvasAccounts(owner) {
    const hash = ownerHash(owner)
    const all = readAllRegistries()
    let entry = all[hash]
    if (!entry || !Array.isArray(entry.accounts)) {
      // 自动承接旧版本已登录的默认 canvas 分区，老用户升级后不丢登录态。
      const legacyStore = session.fromPartition('persist:erp-browser-' + partitionKey(owner, 'gemini', 'canvas'))
      const hasLegacy = await hasGoogleAuthCookies(legacyStore)
      const seeded = hasLegacy === true
        ? [normalizeAccountRow({ id: 'canvas', profile: 'canvas', status: 'unknown', enabled: true })]
        : []
      entry = { accounts: seeded }
      if (seeded.length) {
        all[hash] = entry
        writeAllRegistries(all)
      }
    }
    const result = []
    for (const raw of entry.accounts) {
      let row
      try { row = normalizeAccountRow(raw) } catch { continue }
      const store = session.fromPartition('persist:erp-browser-' + partitionKey(owner, 'gemini', row.profile))
      const hasCookies = await hasGoogleAuthCookies(store)
      const status = hasCookies === false && row.status === 'signed_in' ? 'signed_out' : row.status
      result.push({ ...row, status, busy: isProfileBusy(owner, row.profile) })
    }
    return { accounts: result }
  }

  async function upsertCanvasAccount(owner, data) {
    const hash = ownerHash(owner)
    const all = readAllRegistries()
    const current = Array.isArray(all[hash]?.accounts) ? all[hash].accounts.map(normalizeAccountRow) : []
    const existing = current.find(item => item.profile === data.profile)
    const next = normalizeAccountRow({
      ...existing,
      ...data,
      enabled: data.enabled !== undefined ? Boolean(data.enabled) : (existing ? existing.enabled : true),
    })
    const duplicateProfiles = []
    const updated = []
    let inserted = false
    for (const item of current) {
      if (item.profile === next.profile) {
        updated.push(next)
        inserted = true
      } else if (next.email && item.email && item.email.toLowerCase() === next.email.toLowerCase()) {
        // 同一 Google 邮箱在新建会话中重新登录时，合并旧重复槽位并清理旧分区。
        next.enabled = item.enabled
        duplicateProfiles.push(item.profile)
      } else {
        updated.push(item)
      }
    }
    if (!inserted) updated.push(next)
    all[hash] = { accounts: updated }
    writeAllRegistries(all)
    for (const duplicate of duplicateProfiles) {
      if (!isProfileBusy(owner, duplicate)) await clearProfilePartition(owner, duplicate).catch(() => {})
    }
    return { account: next, ...(await listCanvasAccounts(owner)) }
  }

  async function logoutCanvasAccount(owner, data) {
    const profile = String(data?.profile || '').trim()
    if (!PROFILE_PATTERN.test(profile)) throw new Error('Google 账号会话标识无效。')
    if (isProfileBusy(owner, profile)) throw new Error('该 Google 账号正在执行修图任务，请等待完成后再取消登录。')
    await clearProfilePartition(owner, profile)
    const hash = ownerHash(owner)
    const all = readAllRegistries()
    const current = Array.isArray(all[hash]?.accounts) ? all[hash].accounts : []
    all[hash] = { accounts: current.filter(item => String(item?.profile || item?.id || '') !== profile) }
    writeAllRegistries(all)
    return listCanvasAccounts(owner)
  }

  async function setCanvasAccountEnabled(owner, data) {
    const profile = String(data?.profile || '').trim()
    if (!PROFILE_PATTERN.test(profile)) throw new Error('Google 账号会话标识无效。')
    const hash = ownerHash(owner)
    const all = readAllRegistries()
    const current = Array.isArray(all[hash]?.accounts) ? all[hash].accounts.map(normalizeAccountRow) : []
    const target = current.find(item => item.profile === profile)
    if (!target) throw new Error('未找到对应的 Google 账号。')
    target.enabled = Boolean(data?.enabled)
    all[hash] = { accounts: current }
    writeAllRegistries(all)
    return listCanvasAccounts(owner)
  }
  function close(page) {
    if (!pages.delete(page.id)) return
    clearTimeout(page.idleTimer)
    for (const popup of page.popups) if (!popup.isDestroyed()) popup.destroy()
    if (page.window && !page.window.isDestroyed()) page.window.destroy()
    if (!page.contents.isDestroyed()) page.contents.close()
  }

  function protect(contents, page) {
    function check(event) {
      if (!allowedNavigation(page.kind, event.url)) {
        event.preventDefault()
        if (page.kind === 'gemini' && contents === page.contents) page.loginNavigationInterrupted = true
      }
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
    const key = partitionKey(owner, kind, profile)
    for (const previous of pages.values()) {
      if (previous.inspecting && previous.key === key) close(previous)
    }
    if (kind === 'gemini') {
      const ready = [...pages.values()].find(page => page.idleTimer && page.key === key)
      if (ready && (ready.url !== url || ready.proxy !== proxy)) close(ready)
      if (ready && pages.has(ready.id) && call.active() && account() === owner) {
        clearTimeout(ready.idleTimer)
        ready.idleTimer = null
        ready.callId = call.id
        return ready.id
      }
    }
    const store = session.fromPartition('persist:erp-browser-' + key)
    if (kind === 'gemini' && !stores.has(store)) {
      store.webRequest.onErrorOccurred(details => {
        if (details.resourceType !== 'mainFrame' || details.error !== 'net::ERR_ABORTED') return
        const page = [...pages.values()].find(item => item.contents.id === details.webContentsId)
        if (page?.kind === 'gemini') page.loginNavigationInterrupted = true
      })
    }
    stores.add(store)
    store.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
    store.setPermissionCheckHandler(() => false)
    await store.setProxy(proxy ? { mode: 'fixed_servers', proxyRules: proxy } : { mode: 'system' })
    if (!call.active() || account() !== owner) throw new Error('ERP 登录会话已切换。')
    const view = new WebContentsView({ webPreferences: { session: store, sandbox: true,
      contextIsolation: true, nodeIntegration: false, backgroundThrottling: false } })
    view.setBounds({ x: 0, y: 0, width: 1100, height: 800 })
    const contents = view.webContents
    const page = { owner, kind, key, proxy, url, callId: call.id, id: randomUUID(), contents, store, view, events: [], popups: new Set() }
    pages.set(page.id, page)
    contents.once('destroyed', () => close(page))
    protect(contents, page)
    try {
      if (development) show(page, 'BROWSING')
      await navigate(page, url)
      return page.id
    } catch (error) {
      if (!development) close(page)
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

  function show(page, reason) {
    const labels = { BROWSING: page.kind === 'gemini' ? 'Gemini' : '1688', CANVAS_PREVIEW: '请打开 Canvas 应用预览', CANVAS_CONNECTION: '正在等待 Canvas 翻译连接' }
    page.windowLabel = labels[reason] || '登录与验证'
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
        if (!win.isDestroyed()) win.setTitle(page.windowLabel + ' · ' + new URL(url).origin)
      })
    }
    page.window.setTitle(page.windowLabel + ' · ' + new URL(page.contents.getURL() || page.url).origin)
    if (!page.window.isVisible()) { page.window.show(); page.view.webContents.focus() }
  }

  async function recoverLogin(page, expression, call) {
    if (!page.loginNavigationInterrupted || page.recoveringLogin ||
        new URL(page.contents.getURL()).hostname !== 'accounts.google.com') return false
    page.loginNavigationInterrupted = false
    page.recoveringLogin = true
    // 提交可能已生效；用同一会话读取 Gemini，避免重发验证码表单。
    const probeView = new WebContentsView({ webPreferences: { session: page.store, sandbox: true,
      contextIsolation: true, nodeIntegration: false, backgroundThrottling: false } })
    const probe = probeView.webContents
    const check = event => { if (!allowedNavigation('gemini', event.url)) event.preventDefault() }
    probe.on('will-navigate', check)
    probe.on('will-redirect', event => { if (event.isMainFrame) check(event) })
    probe.setWindowOpenHandler(() => ({ action: 'deny' }))
    let timer, probeWindow
    try {
      if (development) {
        probeWindow = new BaseWindow({ parent: page.window, width: 850, height: 600, show: false, title: 'Gemini 登录状态检查' })
        probeWindow.contentView.addChildView(probeView)
        const fit = () => {
          const [width, height] = probeWindow.getContentSize()
          probeView.setBounds({ x: 0, y: 0, width, height })
        }
        fit()
        probeWindow.on('resize', fit)
        probeWindow.once('closed', () => { if (!probe.isDestroyed()) probe.close() })
        probeWindow.showInactive()
      }
      const state = await Promise.race([
        probe.loadURL('https://gemini.google.com/app').then(() => probe.executeJavaScript(expression)),
        new Promise(resolve => { timer = setTimeout(() => resolve(null), 10000) }),
      ])
      if (!state?.signedIn || !call.active() || account() !== page.owner || !pages.has(page.id)) return false
      await navigate(page, page.url)
      return true
    } catch {
      return false
    } finally {
      clearTimeout(timer)
      if (probeWindow && !probeWindow.isDestroyed()) probeWindow.destroy()
      if (!probe.isDestroyed()) probe.close()
      page.recoveringLogin = false
    }
  }

  async function handle(action, data, owner, call) {
    if (!owner || account() !== owner) throw new Error('ERP 登录会话已失效。')
    if (action === 'open') return open(data, owner, call)
    if (action === 'canvas_accounts_list') return listCanvasAccounts(owner)
    if (action === 'canvas_account_upsert') return upsertCanvasAccount(owner, data)
    if (action === 'canvas_account_logout') return logoutCanvasAccount(owner, data)
    if (action === 'canvas_account_set_enabled') return setCanvasAccountEnabled(owner, data)
    if (action === 'list') return [...pages.values()].filter(p => p.owner === owner && p.kind === '1688' && !p.inspecting)
      .map(p => ({ id: p.id, source_url: p.url, type: 'page', url: p.contents.getURL(), title: p.contents.getTitle(),
        login_popup_closed: !!p.loginPopupClosed, visited_login: !!p.visitedLogin && !p.returnedFromHome }))
    const page = pages.get(data.page)
    if (!page || page.inspecting || page.owner !== owner || page.contents.isDestroyed()) throw new Error('原验证页面已关闭，请重新开始。')
    if (page.kind === 'gemini' && page.callId !== call.id) throw new Error('此 Canvas 页面属于其他操作。')
    if (action === 'release_canvas' && page.kind === 'gemini') {
      // 保留已打开的预览，让预检与后续图片共用它。空闲、切换账号或退出时释放。
      if (!development) {
        page.window?.hide()
        for (const popup of page.popups) if (!popup.isDestroyed()) popup.destroy()
      }
      const control = page.contents.debugger
      if (control?.isAttached()) {
        control.removeListener('message', page.debuggerListener)
        control.detach()
      }
      page.events.length = 0
      page.callId = null
      page.idleTimer = setTimeout(() => close(page), canvasIdleMs)
      page.idleTimer.unref()
      return
    }
    if (action === 'recover_login' && page.kind === 'gemini') return recoverLogin(page, data.expression, call)
    if (action === 'resume' && page.kind === '1688') {
      page.callId = call.id
      return
    }
    if (action === 'show') {
      show(page, data.reason)
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
      if (!development) {
        for (const popup of page.popups) popup.hide()
        page.window?.hide()
      }
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
      page.debuggerListener = (_event, method, params, sessionId) => {
        if (page.kind === 'gemini' && ['Target.attachedToTarget', 'Runtime.executionContextCreated'].includes(method))
          page.events.push({ method, params, sessionId })
      }
      control.on('message', page.debuggerListener)
    }
    const result = await control.sendCommand(data.method, data.params, data.session_id || undefined)
    return { result, events: data.collect_events ? page.events.splice(0) : [] }
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
  function finish(callId, keepId, failed = false) {
    for (const page of pages.values()) {
      if (page.callId !== callId) continue
      if (page.id === keepId && page.kind === '1688' && page.window && !page.window.isDestroyed()) page.callId = null
      else if (development && failed && page.window && !page.window.isDestroyed()) {
        // 留下失败现场供人工查看，后续任务不得接管这个页面。
        page.callId = null
        page.inspecting = true
      } else close(page)
    }
  }
  return { handle, finish, reset, flush, get generation() { return generation } }
}

module.exports = { createEmbeddedBrowser, allowedNavigation, CANVAS_IDLE_MS }
