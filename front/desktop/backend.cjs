const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const readline = require('node:readline')

function backendEnvironment(source = process.env) {
  // 只继承操作系统环境，开发端口、模型密钥和 Python 路径不能串入桌面实例。
  const allowed = new Set(['HOME', 'USER', 'LOGNAME', 'PATH', 'SHELL', 'TMPDIR', 'TMP', 'TEMP',
    'LANG', 'LC_ALL', 'LC_CTYPE', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'COMSPEC',
    'APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'PROGRAMFILES', 'ProgramFiles', 'ProgramFiles(x86)'])
  return Object.fromEntries(Object.entries(source).filter(([key]) => allowed.has(key)))
}

function backendExecutable(resourcesPath) {
  return path.join(resourcesPath, 'backend', process.platform === 'win32' ? 'champion-erp-backend.exe' : 'champion-erp-backend')
}

function startBackend(executable, dataDir, { onUnexpectedExit = () => {}, timeoutMs = 60000 } = {}) {
  const logDir = path.join(dataDir, 'data', 'logs')
  fs.mkdirSync(logDir, { recursive: true, mode: 0o700 })
  const logPath = path.join(logDir, 'desktop-backend.log')
  const log = fs.openSync(logPath, 'a', 0o600)
  const child = spawn(executable, ['--data-dir', dataDir], {
    cwd: dataDir, env: backendEnvironment(), stdio: ['pipe', 'pipe', log], windowsHide: true,
  })
  fs.closeSync(log)
  let stopping = false
  let exited = false
  let ready = false
  let stopPromise
  const exitPromise = new Promise((resolve) => {
    child.once('exit', (code, signal) => {
      exited = true
      resolve()
      if (ready && !stopping) onUnexpectedExit(new Error(`后端意外退出（${code ?? signal}）。日志：${logPath}`))
    })
    child.once('error', () => { exited = true; resolve() })
  })
  function stop() {
    if (stopPromise) return stopPromise
    stopping = true
    stopPromise = (async () => {
      if (exited) return
      child.stdin.end()
      const terminate = setTimeout(() => child.kill('SIGTERM'), 10000)
      const force = setTimeout(() => child.kill('SIGKILL'), 15000)
      await exitPromise
      clearTimeout(terminate)
      clearTimeout(force)
    })()
    return stopPromise
  }
  child.stdin.on('error', () => {})
  const started = new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`后端启动超时。日志：${logPath}`))
      void stop()
    }, timeoutMs)
    const rejectStart = (error) => { clearTimeout(timer); reject(error) }
    child.once('error', rejectStart)
    child.once('exit', (code) => {
      if (!ready) rejectStart(new Error(`后端启动失败（${code}）。日志：${logPath}`))
    })
    const lines = readline.createInterface({ input: child.stdout })
    lines.on('line', (line) => {
      if (!line.startsWith('CHAMPION_ERP_READY ')) return
      try {
        const message = JSON.parse(line.slice('CHAMPION_ERP_READY '.length))
        const url = new URL(message.url)
        if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || message.pid !== child.pid) {
          throw new Error('后端返回的地址或进程身份无效。')
        }
        ready = true
        clearTimeout(timer)
        resolve(message.url.replace(/\/$/, ''))
      } catch (error) {
        rejectStart(error)
        void stop()
      }
    })
  })
  return { started, stop, pid: child.pid, logPath }
}

module.exports = { backendEnvironment, backendExecutable, startBackend }
