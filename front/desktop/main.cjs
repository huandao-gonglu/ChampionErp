const { app, BrowserWindow, dialog, ipcMain, Menu, shell } = require('electron')
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')
const { URL } = require('node:url')
const { runtimeExecutable, startRuntime } = require('./runtime.cjs')
const { normalizeBackendUrl, connectDesktop } = require('./connection.cjs')
const { registerLocalConfig } = require('./local-config.cjs')
const { registerLicenseDirectory } = require('./licenses.cjs')
const { AccountSession, proxyBackendRequest } = require('./account-session.cjs')
const { NewAPIBrowserBridge, registerNewAPIBridge } = require('./newapi.cjs')

const { name: packageName, productName } = require('../package.json')

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

let frontendServer
let newapiBridge
let runtime
let desktopConnection
let quitting = false
const accountSession = new AccountSession()

function notifyConnectionChanged() {
  for (const win of BrowserWindow.getAllWindows()) {
    win.webContents.send('desktop:connection-changed')
  }
}

app.on('second-instance', () => {
  const win = BrowserWindow.getAllWindows()[0]
  if (win) {
    if (win.isMinimized()) win.restore()
    win.focus()
  }
})

async function initializeDesktop() {
  // 沿用原桌面资料根目录，浏览器登录资料不搬入统一后端。
  const dataDir = path.join(app.getPath('userData'), 'backend')
  const options = { onUnexpectedExit: (error) => {
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
  throw new Error(`请在 ${configPath} 设置 backend_url，或通过 CHAMPION_ERP_BACKEND_URL 指定统一业务后端地址。`)
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

function proxyToBackend(req, res, backendBaseUrl) {
  proxyBackendRequest(req, res, backendBaseUrl, {
    session: accountSession,
    connectionId: () => desktopConnection?.id || '',
    onLogin: async () => {
      await desktopConnection?.stop()
      desktopConnection = await connectDesktop(backendBaseUrl, runtime, {
        session: accountSession,
        onDisconnected: error => {
          notifyConnectionChanged()
          if (!quitting && accountSession.cookie) dialog.showErrorBox(`${productName} 本机连接已断开`, `${error.message}。请重新登录后重试。`)
        },
      })
      notifyConnectionChanged()
    },
    onLogout: () => { void desktopConnection?.stop(); desktopConnection = undefined },
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
    if (shouldProxy(requestUrl.pathname)) {
      proxyToBackend(req, res, backendBaseUrl)
      return
    }
    serveFrontend(req, res, requestUrl, frontendRoot)
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
  const { url } = await startFrontendServer()
  const win = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1120,
    minHeight: 720,
    title: productName,
    icon: path.join(__dirname, 'assets', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  registerLocalConfig({ ipcMain, win, frontendUrl: url, runtime })
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

app.whenReady().then(async () => {
  if (!ownsInstance) return
  await initializeDesktop()
  await createWindow()
}).catch((error) => {
  dialog.showErrorBox(`${productName} 启动失败`, error instanceof Error ? error.message : String(error))
  app.quit()
})

app.on('window-all-closed', () => {
  app.quit()
})

app.on('before-quit', (event) => {
  if (frontendServer) {
    frontendServer.close()
    frontendServer = undefined
  }
  if (runtime && !quitting) {
    event.preventDefault()
    quitting = true
    void (async () => { await desktopConnection?.stop(); await runtime.stop() })().finally(() => app.quit())
  }
})
