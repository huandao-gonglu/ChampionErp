const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const readline = require('node:readline')

function runtimeEnvironment(source = process.env) {
  const allowed = new Set(['HOME', 'USER', 'LOGNAME', 'PATH', 'SHELL', 'TMPDIR', 'TMP', 'TEMP',
    'LANG', 'LC_ALL', 'LC_CTYPE', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'COMSPEC',
    'APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'PROGRAMFILES', 'ProgramFiles', 'ProgramFiles(x86)',
    'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY'])
  return Object.fromEntries(Object.entries(source).filter(([key]) => allowed.has(key)))
}

function runtimeExecutable(resourcesPath) {
  return path.join(resourcesPath, 'runtime', process.platform === 'win32' ? 'champion-erp-runtime.exe' : 'champion-erp-runtime')
}

function startRuntime(executable, dataDir, { args = [], cwd = dataDir, onUnexpectedExit = () => {}, timeoutMs = 30000 } = {}) {
  fs.mkdirSync(dataDir, { recursive: true, mode: 0o700 })
  const logPath = path.join(dataDir, 'desktop-runtime.log')
  const log = fs.openSync(logPath, 'a', 0o600)
  const child = spawn(executable, [...args, '--data-dir', dataDir], {
    cwd, env: runtimeEnvironment(), stdio: ['pipe', 'pipe', log], windowsHide: true,
  })
  fs.closeSync(log)
  const pending = new Map()
  let stopping = false
  let exited = false
  let ready = false
  let stopPromise
  let resolveReady, rejectReady
  const started = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject })
  const timer = setTimeout(() => { rejectReady(new Error(`本机执行器启动超时。日志：${logPath}`)); void stop() }, timeoutMs)
  const exitPromise = new Promise((resolve) => {
    function finished(error) {
      if (exited) return
      exited = true
      clearTimeout(timer)
      rejectReady(error)
      for (const call of pending.values()) call.reject(error)
      pending.clear()
      resolve()
      if (ready && !stopping) onUnexpectedExit(error)
    }
    child.once('exit', (code, signal) => finished(new Error(`本机执行器已退出（${code ?? signal}）。日志：${logPath}`)))
    child.once('error', finished)
  })
  const lines = readline.createInterface({ input: child.stdout, crlfDelay: Infinity })
  lines.on('line', (line) => {
    try {
      const reply = JSON.parse(line)
      if (reply.ready === true && reply.pid === child.pid && !ready) {
        ready = true
        clearTimeout(timer)
        resolveReady()
        return
      }
      const call = pending.get(reply.id)
      if (!call) return
      if (reply.asset) {
        call.assets = call.assets.then(() => call.onAsset(reply.asset))
        // 异步上传失败在最终回执中统一报告，避免未处理的 Promise 拒绝。
        call.assets.catch(() => {})
        return
      }
      if (typeof reply.ok !== 'boolean' || (reply.ok && (!reply.result || typeof reply.result !== 'object' || Array.isArray(reply.result)))) {
        throw new Error('本机执行器回执无效。')
      }
      pending.delete(reply.id)
      call.assets.then(() => call.resolve(reply), call.reject)
    } catch (error) {
      for (const call of pending.values()) call.reject(error)
      pending.clear()
      void stop()
    }
  })
  child.stdin.on('error', () => {})
  function send(value) {
    if (exited || stopping) throw new Error('本机执行器未连接。')
    child.stdin.write(`${JSON.stringify(value)}\n`)
  }
  function perform(message, onAsset = async () => {}) {
    if (!ready || pending.has(message.id)) return Promise.reject(new Error('本机执行器未就绪或调用重复。'))
    return new Promise((resolve, reject) => {
      pending.set(message.id, { resolve, reject, onAsset, assets: Promise.resolve() })
      try { send(message) } catch (error) { pending.delete(message.id); reject(error) }
    })
  }
  function cancel(id) {
    if (exited || stopping) return
    send({ id, cancel: true })
  }
  function stop() {
    if (stopPromise) return stopPromise
    stopping = true
    stopPromise = (async () => {
      if (exited) return
      child.stdin.end()
      const terminate = setTimeout(() => child.kill('SIGTERM'), 3000)
      const force = setTimeout(() => child.kill('SIGKILL'), 7000)
      await exitPromise
      clearTimeout(terminate)
      clearTimeout(force)
    })()
    return stopPromise
  }
  return { started, perform, cancel, stop, pid: child.pid, logPath }
}

module.exports = { runtimeEnvironment, runtimeExecutable, startRuntime }
