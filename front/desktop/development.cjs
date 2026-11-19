const { spawn } = require('node:child_process')
const http = require('node:http')
const { DEV_DESKTOP_SCHEME } = require('./deep-links.cjs')

// 开发服务直接管理自己的 Electron 子进程，链接始终通过父子进程通道交接。
function createDevelopmentDesktop({ root, frontendUrl, backendUrl, spawnProcess = spawn }) {
  let child
  let starting
  let ready = false

  function start() {
    if (starting) return starting
    const environment = { ...process.env, CHAMPION_ERP_BACKEND_URL: backendUrl, CHAMPION_ERP_DEV_FRONTEND_URL: frontendUrl }
    delete environment.ELECTRON_RUN_AS_NODE
    child = spawnProcess(require('electron'), [root], { cwd: root, env: environment, stdio: ['ignore', 'inherit', 'inherit', 'ipc'] })
    const current = child
    starting = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error('开发桌面启动超时，请检查开发服务日志。'))
        current.kill()
      }, 45000)
      current.on('message', message => {
        if (message?.type === 'desktop-ready') {
          clearTimeout(timer)
          ready = true
          resolve()
        }
      })
      function failed(error) {
        clearTimeout(timer)
        if (child === current) { child = undefined; starting = undefined; ready = false }
        reject(error)
      }
      current.once('error', failed)
      current.once('exit', () => failed(new Error('开发桌面已退出，请重新打开。')))
    })
    return starting
  }

  async function open(requestId) {
    await start()
    const url = `${DEV_DESKTOP_SCHEME}://continue?request=${requestId}&origin=${encodeURIComponent(backendUrl)}`
    await new Promise((resolve, reject) => child.send({ type: 'desktop-handoff', url }, error => error ? reject(error) : resolve()))
  }

  async function stop() {
    const current = child
    if (!current) return
    await new Promise(resolve => {
      const timer = setTimeout(() => current.kill('SIGKILL'), 10000)
      current.once('exit', () => { clearTimeout(timer); resolve() })
      if (current.connected) current.send({ type: 'desktop-shutdown' }, error => { if (error) current.kill() })
      else current.kill()
    })
  }

  return { start, open, stop, get ready() { return ready } }
}

function desktopDevelopment({ enabled, backendUrl }) {
  let desktop
  return {
    name: 'desktop-development',
    apply: 'serve',
    configureServer(server) {
      const port = server.config.server.port
      const frontendUrl = `http://127.0.0.1:${port}`
      desktop = createDevelopmentDesktop({ root: server.config.root, frontendUrl, backendUrl })
      server.middlewares.use(async (request, response, next) => {
        if (!['/__dev/desktop-ready', '/__dev/desktop-handoff'].includes(request.url)) return next()
        function reply(status, error = '') {
          response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
          response.end(JSON.stringify({ ok: status === 200, error }))
        }
        if (request.headers.host !== new URL(frontendUrl).host) return reply(403, '开发请求来源无效。')
        if (!enabled) return reply(503, '请使用 scripts/dev.sh 或 pnpm desktop:dev 启动开发桌面。')
        if (request.url === '/__dev/desktop-ready' && request.method === 'GET') {
          return reply(desktop.ready ? 200 : 503, desktop.ready ? '' : '开发桌面正在启动。')
        }
        if (request.method !== 'POST' || request.headers.origin !== frontendUrl
            || request.headers['content-type'] !== 'application/json') return reply(403, '开发请求来源无效。')
        try {
          let raw = ''
          for await (const chunk of request) {
            raw += chunk
            if (raw.length > 1024) { reply(413, '开发打开请求过大。'); return }
          }
          const body = JSON.parse(raw)
          if (!body || Object.keys(body).length !== 1 || typeof body.request_id !== 'string' || !/^[\w-]{43}$/.test(body.request_id)) {
            return reply(400, '桌面打开请求无效。')
          }
          await desktop.open(body.request_id)
          reply(200)
        } catch (error) {
          reply(503, error instanceof Error ? error.message : '无法打开开发桌面。')
        }
      })
      if (enabled) server.httpServer.once('listening', () => {
        void desktop.start().catch(error => server.config.logger.error(error.message))
      })
    },
    async closeBundle() { await desktop?.stop() },
  }
}

// 桌面继续通过自己的 HTTP 入口和主进程代理访问 API；只有页面资源交给 Vite。
function proxyDevelopmentFrontend(request, response, frontendUrl) {
  const target = new URL(frontendUrl)
  const upstream = http.request({ hostname: target.hostname, port: target.port, path: request.url, method: request.method,
    headers: { accept: request.headers.accept || '*/*', host: target.host } }, result => {
    response.writeHead(result.statusCode, result.headers)
    result.pipe(response)
  })
  upstream.on('error', () => {
    if (!response.headersSent) response.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' })
    response.end('无法连接前端开发服务，请重新运行 scripts/dev.sh。')
  })
  response.on('close', () => upstream.destroy())
  request.pipe(upstream)
}

module.exports = { createDevelopmentDesktop, desktopDevelopment, proxyDevelopmentFrontend }
