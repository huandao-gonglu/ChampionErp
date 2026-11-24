const http = require('node:http')
const https = require('node:https')

function normalizeBackendUrl(value) {
  const url = new URL(value)
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) {
    throw new Error('业务后端地址必须是 HTTP 或 HTTPS 的站点地址。')
  }
  return url.origin
}

const TRANSIENT_STATUSES = new Set([408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526])

function isTerminalError(error) {
  if (!error) return false
  if (error.name === 'AbortError' || error.code === 'ABORT_ERR') return true
  if ([401, 403, 426].includes(error.status)) return true
  return error.status === 409 && error.code === 'DESKTOP_UNAVAILABLE'
}

function isTransientError(error) {
  if (!error || isTerminalError(error)) return false
  if (error.transient) return true
  if (TRANSIENT_STATUSES.has(error.status)) return true
  return !error.status
}

function abortableDelay(ms, signal) {
  if (ms <= 0) return Promise.resolve()
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(signal.reason || Object.assign(new Error('操作已取消。'), { name: 'AbortError' })); return }
    const timer = setTimeout(() => { signal?.removeEventListener('abort', onAbort); resolve() }, ms)
    timer.unref?.()
    function onAbort() {
      clearTimeout(timer)
      reject(signal.reason || Object.assign(new Error('操作已取消。'), { name: 'AbortError' }))
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

function requestJson(baseUrl, pathname, data, connectionId = '', timeoutMs = 25000, signal, session, client = '') {
  const url = new URL(pathname, baseUrl)
  return new Promise((resolve, reject) => {
    const raw = data === undefined ? undefined : JSON.stringify(data)
    const request = (url.protocol === 'https:' ? https : http).request(url, {
      method: raw === undefined ? 'GET' : 'POST', signal,
      // 禁用默认 keepAlive 连接池，避免本地代理或 Cloudflare 网关下复用已关闭的半开 TCP 连接。
      agent: false,
      headers: {
        Connection: 'close',
        ...(raw === undefined ? {} : { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(raw) }),
        ...session?.headers(),
        ...(client ? { 'X-Champion-Client': client } : {}),
        ...(connectionId ? { 'X-Champion-Desktop': connectionId } : {}) },
    }, (response) => {
      let body = ''
      response.setEncoding('utf8')
      response.on('data', (chunk) => {
        body += chunk
        if (body.length > 96 * 1024 * 1024) response.destroy(new Error('后端回执超过大小上限。'))
      })
      response.on('error', reject)
      response.on('end', () => {
        let result
        try {
          result = JSON.parse(body)
        } catch {
          const error = new Error(`后端返回非 JSON 响应（HTTP ${response.statusCode}）`)
          error.status = response.statusCode
          error.transient = TRANSIENT_STATUSES.has(response.statusCode) || response.statusCode >= 500
          reject(error)
          return
        }
        if (response.statusCode !== 200 || !result.ok) {
          const error = new Error(result.error || `后端返回 HTTP ${response.statusCode}`)
          error.code = result.error_code
          error.status = response.statusCode
          error.transient = TRANSIENT_STATUSES.has(response.statusCode)
          reject(error)
          return
        }
        resolve(result)
      })
    })
    request.setTimeout(timeoutMs, () => request.destroy(Object.assign(new Error('业务后端连接超时。'), { transient: true })))
    request.on('error', reject)
    request.end(raw)
  })
}

async function connectDesktop(baseUrl, runtime, {
  onDisconnected = () => {}, session, client = '',
  pollTimeoutMs = 35000, pollRetryBudgetMs = 40000, resultTimeoutMs = 90000,
  pollRetryDelaysMs = [500, 1000, 2000, 3000], resultRetryDelaysMs = [1000, 2000, 4000],
} = {}) {
  const send = (pathname, data, connectionId = '', timeoutMs = 25000, signal) => requestJson(baseUrl, pathname, data, connectionId, timeoutMs, signal, session, client)
  const registration = await send('/api/desktop/connect', {})
  const id = registration.connection_id
  if (typeof id !== 'string' || id.length < 32) throw new Error('桌面连接标识无效。')
  const active = new Set()
  const controller = new AbortController()
  let stopped = false
  const connection = {
    get id() { return stopped ? '' : id },
    async stop({ notifyServer = true } = {}) {
      if (stopped) return
      stopped = true
      controller.abort()
      for (const callId of active) runtime.cancel(callId)
      if (notifyServer) {
        await send('/api/desktop/disconnect', {}, id, 3000).catch(() => {})
      }
    },
  }
  async function sendIdempotent(pathname, data, timeoutMs) {
    let attempt = 0
    while (true) {
      try {
        return await send(pathname, data, id, timeoutMs, controller.signal)
      } catch (error) {
        if (stopped || !isTransientError(error) || attempt >= resultRetryDelaysMs.length) throw error
        const waitMs = resultRetryDelaysMs[attempt++]
        await abortableDelay(waitMs, controller.signal)
      }
    }
  }
  async function execute(call) {
    active.add(call.id)
    try {
      let reply
      try {
        if (call.operation === 'config.write') throw new Error('业务后端不能修改本机连接配置，请在桌面设置中保存。')
        reply = await runtime.perform(call, (asset) => sendIdempotent('/api/desktop/asset', { id: call.id, asset }, resultTimeoutMs))
      } catch (error) {
        reply = { id: call.id, ok: false, error: error.message, error_code: 'DESKTOP_RESULT_UNKNOWN', outcome_unknown: true }
      }
      // 服务端 /api/desktop/result 具备幂等性，仅重试传输回执而不重放本机执行。
      if (!stopped) await sendIdempotent('/api/desktop/result', reply, resultTimeoutMs)
    } catch (error) {
      if (!stopped && isTerminalError(error)) {
        await connection.stop({ notifyServer: false })
        onDisconnected(error)
      }
    } finally { active.delete(call.id) }
  }
  async function poll() {
    let transientSince = 0
    let transientAttempt = 0
    while (!stopped) {
      try {
        const result = await send('/api/desktop/poll', {}, id, pollTimeoutMs, controller.signal)
        if (!Array.isArray(result.calls)) throw new Error('后端调用列表无效。')
        transientSince = 0
        transientAttempt = 0
        for (const call of result.calls) {
          if (stopped) break
          if (call.cancel) runtime.cancel(call.id)
          else void execute(call)
        }
      } catch (error) {
        if (stopped) break
        if (isTransientError(error)) {
          if (!transientSince) transientSince = Date.now()
          if (Date.now() - transientSince < pollRetryBudgetMs) {
            const waitMs = pollRetryDelaysMs[Math.min(transientAttempt++, pollRetryDelaysMs.length - 1)]
            try {
              await abortableDelay(waitMs, controller.signal)
              continue
            } catch { break }
          }
        }
        if (!stopped) {
          // 服务端已失效或连续断网超时后不再主动发 /disconnect，避免误杀租期内的在途任务。
          await connection.stop({ notifyServer: false })
          onDisconnected(error)
        }
      }
    }
  }
  void poll()
  return connection
}

// 一个 ERP 登录会话只维护一个连接。只重建传输通道，已经发出的业务操作不重放。
function createAccountConnection(baseUrl, runtime, { session, client, onChange, connectOptions } = {}) {
  const headers = session.headers()
  const auth = { headers: () => headers }
  let current, pending, timer, stopped = false, retryMs = 1000

  function retry(error) {
    if (stopped || timer || [401, 403, 426].includes(error?.status)) return
    timer = setTimeout(() => {
      timer = undefined
      ensure().catch(() => {})
    }, retryMs)
    timer.unref()
    retryMs = Math.min(retryMs * 2, 15000)
  }

  async function restore(validate) {
    const previous = current
    if (previous?.id && validate) {
      try {
        await requestJson(baseUrl, '/api/operation-availability', undefined, previous.id, 5000, undefined, auth, client)
      } catch (error) {
        if (error.status !== 409 || error.code !== 'DESKTOP_UNAVAILABLE') throw error
        current = undefined
        await previous.stop({ notifyServer: false })
      }
    }
    if (stopped) throw new Error('桌面会话已结束。')
    if (current?.id) return
    const link = await connectDesktop(baseUrl, runtime, { ...connectOptions, session: auth, client,
      onDisconnected(error) {
        if (stopped || current !== link) return
        current = undefined
        onChange()
        retry(error)
      },
    })
    if (stopped) {
      await link.stop()
      throw new Error('桌面会话已结束。')
    }
    current = link
    retryMs = 1000
    onChange()
  }

  function ensure(validate = false) {
    if (stopped) return Promise.reject(new Error('桌面会话已结束。'))
    if (pending) return pending
    if (!validate && current?.id) return Promise.resolve()
    clearTimeout(timer)
    timer = undefined
    pending = restore(validate).catch(error => { retry(error); throw error })
      .finally(() => { pending = undefined })
    return pending
  }

  return {
    get id() { return current?.id || '' },
    ensure,
    async stop() {
      stopped = true
      clearTimeout(timer)
      await current?.stop()
      await pending?.catch(() => {})
    },
  }
}

module.exports = { normalizeBackendUrl, requestJson, connectDesktop, createAccountConnection }
