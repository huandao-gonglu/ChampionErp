const { app, BaseWindow, BrowserWindow, WebContentsView, session: electronSession, dialog, ipcMain, Menu, shell, safeStorage, autoUpdater } = require('electron')
const fs = require('node:fs')
const { randomBytes } = require('node:crypto')
const { createDesktopUpdates } = require('./updates.cjs')
const { canUseNativeUpdates } = require('./update-signature.cjs')
const http = require('node:http')
const path = require('node:path')
const { URL } = require('node:url')
const { createEmbeddedBrowser } = require('./embedded-browser.cjs')
const { mountLayout } = require('./verification/layout.cjs')
const { runtimeExecutable, startRuntime } = require('./runtime.cjs')
const { normalizeBackendUrl, createAccountConnection } = require('./connection.cjs')
const { registerLocalConfig } = require('./local-config.cjs')
const { registerLicenseDirectory } = require('./licenses.cjs')
const { SavedLogin, registerSavedLogin } = require('./saved-login.cjs')
const { AccountSession, proxyBackendRequest } = require('./account-session.cjs')
const { registerAlibabaAuthorization } = require('./alibaba-authorization.cjs')
const { NewAPIBrowserBridge, registerNewAPIBridge } = require('./newapi.cjs')
const { DESKTOP_SCHEME, DEV_DESKTOP_SCHEME, parseDesktopLink, desktopLinkArgument, registerDesktopHandoff } = require('./deep-links.cjs')
const { proxyDevelopmentFrontend } = require('./development.cjs')

const { name: packageName, productName, desktopUpdateOrigin } = require('../package.json')
const desktopScheme = app.isPackaged ? DESKTOP_SCHEME : DEV_DESKTOP_SCHEME
const desktopName = app.isPackaged ? productName : `${productName} 开发版`
const developmentFrontend = !app.isPackaged && process.env.CHAMPION_ERP_DEV_FRONTEND_URL
if (developmentFrontend && !/^http:\/\/127\.0\.0\.1:\d+$/.test(developmentFrontend)) {
  throw new Error('桌面开发页面必须使用本机 Vite 地址。')
}
// Electron 会把应用名写入 User-Agent。中文开发标记只用于窗口标题。
if (!app.isPackaged) app.setName('gongluErpDev')

// 显示名称可以更新，正式版和开发版各自沿用原有的配置目录。
app.setPath('userData', path.join(app.getPath('appData'), app.isPackaged ? 'Champion ERP' : packageName))
const ownsInstance = app.requestSingleInstanceLock()
if (!ownsInstance) app.quit()

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
}

const BACKEND_PROXY_PREFIXES = [
  '/api',
  '/file',
  '/auth/mercadolibre',
  '/auth/ozon',
  '/auth/alibaba',
]

let updates
let starting = true
let frontendServer
let newapiBridge
let runtime
let embeddedBrowser
let mainWindow
let vault
let desktopConnection
let quitting = false
const localRequestKey = randomBytes(32).toString('hex')
const accountSession = new AccountSession()
const desktopClient = app.isPackaged ? `${process.platform}-${process.arch}/${app.getVersion()}` : ''

function notifyConnectionChanged() {
  mainWindow?.webContents.send('desktop:connection-changed')
}

let pendingHandoff = ''
let pendingLinkError = ''
function receiveDesktopLink(value) {
  try {
    pendingHandoff = parseDesktopLink(value, getBackendBaseUrl(), desktopScheme)
    mainWindow?.webContents.send('desktop:handoff', pendingHandoff)
    const win = mainWindow
    if (win) {
      if (win.isMinimized()) win.restore()
      win.show()
      win.focus()
    }
  } catch (error) {
    if (app.isReady()) dialog.showErrorBox('无法打开桌面页面', error.message)
    else pendingLinkError = error.message
  }
}

// macOS 的启动链接可能早于 ready 到达，先保存，待页面和登录完成后接收。
app.on('open-url', (event, value) => {
  event.preventDefault()
  receiveDesktopLink(value)
})
const initialLink = desktopLinkArgument(process.argv, desktopScheme)
if (ownsInstance && initialLink) receiveDesktopLink(initialLink)

app.on('second-instance', (_event, argv) => {
  const link = desktopLinkArgument(argv, desktopScheme)
  if (link) receiveDesktopLink(link)
  const win = mainWindow
  if (win) {
    if (win.isMinimized()) win.restore()
    win.focus()
  }
})

if (developmentFrontend) {
  process.on('message', message => {
    if (message?.type === 'desktop-handoff') receiveDesktopLink(message.url)
    if (message?.type === 'desktop-shutdown') app.quit()
  })
  // 启动器意外退出也要释放开发桌面及本机执行器。
  process.on('disconnect', () => app.quit())
  process.on('SIGINT', () => app.quit())
  process.on('SIGTERM', () => app.quit())
}

async function initializeDesktop() {
  // 沿用原桌面资料根目录，浏览器登录资料不搬入统一后端。
  const dataDir = path.join(app.getPath('userData'), 'backend')
  const backendOrigin = getBackendBaseUrl()
  const browserOwner = () => accountSession.email && JSON.stringify([backendOrigin, accountSession.email])
  embeddedBrowser = createEmbeddedBrowser({ BaseWindow, WebContentsView, session: electronSession,
    parent: () => mainWindow, account: browserOwner, layoutVerificationWindow: mountLayout, development: !app.isPackaged })
  const options = { browser: embeddedBrowser, browserOwner, onUnexpectedExit: (error) => {
    void desktopConnection?.stop()
    notifyConnectionChanged()
    dialog.showErrorBox(`${productName} 本机执行器已停止`, error.message)
  } }
  if (app.isPackaged) runtime = startRuntime(runtimeExecutable(process.resourcesPath), dataDir, options)
  else {
    const root = path.resolve(__dirname, '../..')
    const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
    runtime = startRuntime(python, dataDir, { ...options, cwd: root, args: ['-m', 'desktop_runtime'] })
  }
  await runtime.started
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    { label: '文件', submenu: [
      ...(updates ? [{ label: '检查桌面更新…', click: () => { void updates.manual() } }] : []),
      { label: '打开本机配置文件夹', click: () => { void shell.openPath(dataDir) } },
      { role: 'quit', label: '退出' },
    ] },
    { role: 'editMenu', label: '编辑' }, { role: 'viewMenu', label: '视图' }, { role: 'windowMenu', label: '窗口' },
  ]))
}

function getBackendBaseUrl() {
  const explicitUrl = process.env.CHAMPION_ERP_BACKEND_URL || process.env.ERP_DESKTOP_BACKEND_URL || process.env.VITE_DEV_PROXY_TARGET
  if (explicitUrl) return normalizeBackendUrl(explicitUrl)
  const configPath = path.join(app.getPath('userData'), 'connection.json')
  if (fs.existsSync(configPath)) return normalizeBackendUrl(JSON.parse(fs.readFileSync(configPath, 'utf8')).backend_url)
  if (!app.isPackaged) return `http://127.0.0.1:${process.env.ERP_PORT || '5000'}`
  return normalizeBackendUrl(desktopUpdateOrigin)
}

function getFrontendRoot() {
  if (app.isPackaged) {
    return path.join(app.getAppPath(), 'dist-desktop')
  }
  return path.resolve(__dirname, '..', 'dist-desktop')
}

function shouldProxy(pathname) {
  return BACKEND_PROXY_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`))
}

function send(res, statusCode, raw, contentType) {
  res.writeHead(statusCode, {
    'Content-Type': contentType,
    'Content-Length': Buffer.byteLength(raw),
  })
  res.end(raw)
}

function sendFrontendFile(res, filePath) {
  fs.readFile(filePath, (error, raw) => {
    if (error) {
      send(res, 404, 'Not found', 'text/plain; charset=utf-8')
      return
    }
    res.writeHead(200, {
      'Content-Type': MIME_TYPES[path.extname(filePath).toLowerCase()] || 'application/octet-stream',
      'Content-Length': raw.length,
    })
    res.end(raw)
  })
}

function serveFrontend(req, res, requestUrl, frontendRoot) {
  let pathname
  try {
    pathname = decodeURIComponent(requestUrl.pathname)
  } catch {
    send(res, 400, 'Bad request', 'text/plain; charset=utf-8')
    return
  }

  const normalizedRoot = path.resolve(frontendRoot)
  const relativePath = pathname.replace(/^\/+/, '')
  const requestedPath = path.resolve(normalizedRoot, relativePath)
  const relativeToRoot = path.relative(normalizedRoot, requestedPath)
  const isInsideRoot = relativeToRoot === '' || (!relativeToRoot.startsWith('..') && !path.isAbsolute(relativeToRoot))

  if (!isInsideRoot) {
    send(res, 403, 'Forbidden', 'text/plain; charset=utf-8')
    return
  }

  if (path.extname(requestedPath) && fs.existsSync(requestedPath) && fs.statSync(requestedPath).isFile()) {
    sendFrontendFile(res, requestedPath)
    return
  }

  const indexPath = path.join(normalizedRoot, 'index.html')
  if (!fs.existsSync(indexPath)) {
    send(
      res,
      500,
      'Desktop frontend is missing. Run `pnpm build:desktop` in the front directory first.',
      'text/plain; charset=utf-8',
    )
    return
  }
  sendFrontendFile(res, indexPath)
}

async function connectAccount(backendBaseUrl) {
  const previous = desktopConnection
  const link = createAccountConnection(backendBaseUrl, runtime, {
    session: accountSession, client: desktopClient, onChange: notifyConnectionChanged,
  })
  desktopConnection = link
  await previous?.stop()
  try { await link.ensure() } catch (error) {
    if ([401, 403, 426].includes(error.status) || desktopConnection !== link) throw error
    // 后端已经接受 ERP 登录。临时断网由当前会话继续重连。
  }
}

async function proxyToBackend(req, res, backendBaseUrl) {
  const path = new URL(req.url, 'http://desktop.invalid').pathname
  const link = desktopConnection
  // 休眠或服务重启后，当前连接标识可能仍在本地，但服务器已经释放它。
  if (link && accountSession.cookie && req.method === 'POST' && ['/api/desktop-handoff/resolve', '/api/collect-batch', '/api/collect-source'].includes(path)) {
    try {
      await link.ensure(true)
    } catch (error) {
      if (!res.destroyed) send(res, error.status || 503, JSON.stringify({
        ok: false, error: [401, 403, 426].includes(error.status) ? error.message : '暂时无法连接桌面服务，请稍后重试。',
        error_code: error.code || 'DESKTOP_UNAVAILABLE',
      }), 'application/json; charset=utf-8')
      return
    }
    if (res.destroyed) return
    if (desktopConnection !== link) {
      send(res, 409, JSON.stringify({ ok: false, error: 'ERP 登录会话已切换，请重新发起操作。' }), 'application/json; charset=utf-8')
      return
    }
  }
  proxyBackendRequest(req, res, backendBaseUrl, {
    session: accountSession, client: desktopClient,
    connectionId: () => desktopConnection?.id || '',
    onLogin: async () => {
      embeddedBrowser.reset()
      vault.write({})
      await connectAccount(backendBaseUrl)
    },
    onLogout: reason => {
      embeddedBrowser.reset()
      vault.forgetSession(reason === '/api/account/password')
      void desktopConnection?.stop()
      desktopConnection = undefined
    },
  })
}

function startFrontendServer() {
  const frontendRoot = getFrontendRoot()
  const backendBaseUrl = getBackendBaseUrl()

  frontendServer = http.createServer((req, res) => {
    const requestUrl = new URL(req.url || '/', 'http://127.0.0.1')
    const ownOrigin = `http://127.0.0.1:${frontendServer.address().port}`
    if (req.headers.host !== new URL(ownOrigin).host) {
      send(res, 403, '请求来源无效', 'text/plain; charset=utf-8'); return
    }
    if (newapiBridge?.handle(req, res)) return
    if ((req.headers.origin && req.headers.origin !== ownOrigin) || req.headers['sec-fetch-site'] === 'cross-site') {
      send(res, 403, '请求来源无效', 'text/plain; charset=utf-8')
      return
    }
    // 传输协议只供主进程使用，渲染页面不能创建或接管桌面连接。
    if (requestUrl.pathname.startsWith('/api/desktop/')) {
      send(res, 403, '不允许访问桌面连接协议', 'text/plain; charset=utf-8')
      return
    }
    if (updates?.blocksRequest(req.method, requestUrl.pathname)) {
      send(res, 426, JSON.stringify({ ok: false, error: '当前桌面版本需要升级。请在文件菜单检查更新。' }),
        'application/json; charset=utf-8')
      return
    }
    if (shouldProxy(requestUrl.pathname)) {
      if (req.headers['x-erp-local-window'] !== localRequestKey) {
        send(res, 403, '仅桌面窗口可调用本机代理', 'text/plain; charset=utf-8')
        return
      }
      delete req.headers['x-erp-local-window']
      proxyToBackend(req, res, backendBaseUrl)
      return
    }
    if (developmentFrontend) proxyDevelopmentFrontend(req, res, developmentFrontend)
    else serveFrontend(req, res, requestUrl, frontendRoot)
  })

  return new Promise((resolve, reject) => {
    frontendServer.on('error', reject)
    frontendServer.listen(0, '127.0.0.1', () => {
      const address = frontendServer.address()
      newapiBridge = new NewAPIBrowserBridge({ backendBaseUrl, frontendUrl: `http://127.0.0.1:${address.port}/`, session: accountSession })
      resolve({
        url: `http://127.0.0.1:${address.port}/`,
        backendBaseUrl,
      })
    })
  })
}

async function createWindow() {
  const { url, backendBaseUrl } = await startFrontendServer()
  vault = new SavedLogin(app.getPath('userData'), backendBaseUrl, safeStorage)
  const saved = vault.read()
  if (saved.cookie && saved.csrf) {
    Object.assign(accountSession, { cookie: saved.cookie, csrf: saved.csrf, email: saved.email })
    try { await connectAccount(backendBaseUrl) }
    catch (error) {
      void desktopConnection?.stop()
      desktopConnection = undefined
      accountSession.clear()
      if (error.status === 401 || error.status === 403 || error.status === 426) vault.forgetSession()
    }
  }
  const win = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1120,
    minHeight: 720,
    title: desktopName,
    icon: path.join(__dirname, 'assets', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  mainWindow = win
  if (!app.isPackaged) win.on('page-title-updated', event => { event.preventDefault(); win.setTitle(desktopName) })
  win.once('closed', () => { mainWindow = undefined; embeddedBrowser.reset() })
  win.webContents.session.webRequest.onBeforeSendHeaders({ urls: [url + '*'] }, (details, callback) => {
    if (details.webContentsId === win.webContents.id) details.requestHeaders['X-ERP-Local-Window'] = localRequestKey
    callback({ requestHeaders: details.requestHeaders })
  })
  registerSavedLogin({ ipcMain, win, url, vault, session: accountSession, defaultAuto: !app.isPackaged })
  registerLocalConfig({ ipcMain, win, frontendUrl: url, runtime })
  registerDesktopHandoff({ ipcMain, win, frontendUrl: url, pending: () => pendingHandoff,
    finish: id => { if (pendingHandoff === id) pendingHandoff = '' } })
  registerAlibabaAuthorization({ ipcMain, win, frontendUrl: url, app, shell })
  registerNewAPIBridge({ ipcMain, win, shell, bridge: newapiBridge })
  registerLicenseDirectory({
    ipcMain, shell, win, frontendUrl: url,
    directory: path.join(app.isPackaged ? process.resourcesPath : getFrontendRoot(), 'licenses'),
  })

  if (process.platform === 'darwin') {
    win.webContents.on('context-menu', (_event, { isEditable, editFlags, frame }) => {
      if (!isEditable || !frame) return
      const menu = Menu.buildFromTemplate([
        { role: 'cut', enabled: editFlags.canCut },
        { role: 'copy', enabled: editFlags.canCopy },
        { role: 'paste', enabled: editFlags.canPaste },
        { type: 'separator' },
        { role: 'selectAll', enabled: editFlags.canSelectAll },
      ])
      // macOS 使用被右键点击的页面框架提供系统自动填充菜单。
      menu.popup({ window: win, frame })
    })
  }

  win.webContents.setWindowOpenHandler(({ url: nextUrl }) => {
    try {
      const parsed = new URL(nextUrl)
      if (parsed.href === new URL('/register', url).href) {
        void shell.openExternal(new URL('/register', backendBaseUrl).href)
        return { action: 'deny' }
      }
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
        shell.openExternal(nextUrl)
        return { action: 'deny' }
      }
    } catch {
      return { action: 'deny' }
    }
    return { action: 'deny' }
  })

  win.webContents.on('will-navigate', (event, nextUrl) => {
    if (new URL(nextUrl).origin === new URL(url).origin) return
    event.preventDefault()
    shell.openExternal(nextUrl)
  })

  await win.loadURL(url)
}

function showDownloadProgress(cancel) {
  const progress = new BrowserWindow({
    width: 410, height: 145, resizable: false, autoHideMenuBar: true, title: '正在下载桌面更新',
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false },
  })
  const page = '<meta charset="utf-8"><style>body{font:15px system-ui;padding:8px 18px}</style>' +
    '<p>正在下载安装包，完成后会校验文件。</p><p>关闭此窗口可取消下载。</p>'
  void progress.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(page)).catch(cancel)
  progress.once('closed', cancel)
  let percent = -1
  return {
    update(value) {
      const next = Math.floor(value * 100)
      if (progress.isDestroyed() || percent === next) return
      percent = next
      progress.setTitle('正在下载桌面更新 · ' + percent + '%')
      progress.setProgressBar(value)
    },
    close() {
      progress.removeListener('closed', cancel)
      if (!progress.isDestroyed()) progress.destroy()
    },
  }
}

app.whenReady().then(async () => {
  if (!ownsInstance) return
  if (app.isPackaged) app.setAsDefaultProtocolClient(DESKTOP_SCHEME)
  if (pendingLinkError) dialog.showErrorBox('无法打开桌面页面', pendingLinkError)
  if (app.isPackaged) {
    updates = createDesktopUpdates({ app, dialog, shell, autoUpdater, origin: desktopUpdateOrigin,
      showProgress: showDownloadProgress, parentWindow: () => mainWindow,
      nativeSigned: () => canUseNativeUpdates(path.resolve(process.resourcesPath, '../..')),
    })
    const result = await updates.startup()
    if (result === 'installing') return
    if (!result) { app.quit(); return }
  }
  await initializeDesktop()
  await createWindow()
  starting = false
  if (developmentFrontend) process.send?.({ type: 'desktop-ready' })
  updates?.watch()
}).catch((error) => {
  dialog.showErrorBox(`${productName} 启动失败`, error instanceof Error ? error.message : String(error))
  app.quit()
})

app.on('window-all-closed', () => {
  if (!starting) app.quit()
})

app.on('before-quit', (event) => {
  updates?.stop()
  if (frontendServer) {
    frontendServer.close()
    frontendServer = undefined
  }
  if (runtime && !quitting) {
    event.preventDefault()
    quitting = true
    void (async () => { await desktopConnection?.stop(); await runtime.stop(); await embeddedBrowser.flush() })().finally(() => app.quit())
  }
})
