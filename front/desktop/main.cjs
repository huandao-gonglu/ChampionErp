const { app, BrowserWindow, dialog, shell } = require('electron')
const fs = require('node:fs')
const http = require('node:http')
const https = require('node:https')
const path = require('node:path')
const { URL } = require('node:url')

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
  '/auth/wildberries',
  '/auth/ozon',
]

let frontendServer

function getBackendBaseUrl() {
  const explicitUrl =
    process.env.CHAMPION_ERP_BACKEND_URL ||
    process.env.ERP_DESKTOP_BACKEND_URL ||
    process.env.VITE_DEV_PROXY_TARGET
  if (explicitUrl) return explicitUrl.replace(/\/+$/, '')
  return `http://127.0.0.1:${process.env.ERP_PORT || '5000'}`
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
  const targetUrl = new URL(req.url || '/', `${backendBaseUrl}/`)
  const transport = targetUrl.protocol === 'https:' ? https : http
  const headers = { ...req.headers, host: targetUrl.host }

  const proxyReq = transport.request(
    targetUrl,
    {
      method: req.method,
      headers,
    },
    (proxyRes) => {
      res.writeHead(proxyRes.statusCode || 502, proxyRes.headers)
      proxyRes.pipe(res)
    },
  )

  proxyReq.on('error', (error) => {
    const message = JSON.stringify({
      ok: false,
      error: `后端未连接：${backendBaseUrl}`,
      detail: error.message,
    })
    res.writeHead(502, {
      'Content-Type': 'application/json; charset=utf-8',
      'Content-Length': Buffer.byteLength(message),
    })
    res.end(message)
  })

  req.pipe(proxyReq)
}

function startFrontendServer() {
  const frontendRoot = getFrontendRoot()
  const backendBaseUrl = getBackendBaseUrl()

  frontendServer = http.createServer((req, res) => {
    const requestUrl = new URL(req.url || '/', 'http://127.0.0.1')
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
      resolve({
        url: `http://127.0.0.1:${address.port}/`,
        backendBaseUrl,
      })
    })
  })
}

async function createWindow() {
  const { url, backendBaseUrl } = await startFrontendServer()
  const win = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1120,
    minHeight: 720,
    title: 'Champion ERP',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

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
    if (nextUrl.startsWith(url) || nextUrl.startsWith(backendBaseUrl)) return
    event.preventDefault()
    shell.openExternal(nextUrl)
  })

  await win.loadURL(url)
}

app.whenReady().then(createWindow).catch((error) => {
  dialog.showErrorBox('Champion ERP 启动失败', error instanceof Error ? error.message : String(error))
  app.quit()
})

app.on('window-all-closed', () => {
  app.quit()
})

app.on('before-quit', () => {
  if (frontendServer) {
    frontendServer.close()
    frontendServer = undefined
  }
})
