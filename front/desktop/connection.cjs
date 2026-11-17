const http = require('node:http')
const https = require('node:https')

function normalizeBackendUrl(value) {
  const url = new URL(value)
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) {
    throw new Error('业务后端地址必须是 HTTP 或 HTTPS 的站点地址。')
  }
  return url.origin
}

function requestJson(baseUrl, pathname, data, connectionId = '', timeoutMs = 25000, signal, session, client = '') {
  const url = new URL(pathname, baseUrl)
  return new Promise((resolve, reject) => {
    const raw = data === undefined ? undefined : JSON.stringify(data)
    const request = (url.protocol === 'https:' ? https : http).request(url, {
      method: raw === undefined ? 'GET' : 'POST', signal, headers: {
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
        try {
          const result = JSON.parse(body)
          if (response.statusCode !== 200 || !result.ok) {
            const error = new Error(result.error || `后端返回 HTTP ${response.statusCode}`)
            error.code = result.error_code
            error.status = response.statusCode
            throw error
          }
          resolve(result)
        } catch (error) { reject(error) }
      })
    })
    request.setTimeout(timeoutMs, () => request.destroy(new Error('业务后端连接超时。')))
    request.on('error', reject)
    request.end(raw)
  })
}

async function connectDesktop(baseUrl, runtime, { onDisconnected = () => {}, session, client = '' } = {}) {
  const send = (pathname, data, connectionId = '', timeoutMs = 25000, signal) => requestJson(baseUrl, pathname, data, connectionId, timeoutMs, signal, session, client)
  const registration = await send('/api/desktop/connect', {})
  const id = registration.connection_id
  if (typeof id !== 'string' || id.length < 32) throw new Error('桌面连接标识无效。')
  const active = new Set()
  const controller = new AbortController()
  let stopped = false
  const connection = {
    get id() { return stopped ? '' : id },
    async stop() {
      if (stopped) return
      stopped = true
      controller.abort()
      for (const callId of active) runtime.cancel(callId)
      // 断开是尽力通知；结果回执从不重试。
      await send('/api/desktop/disconnect', {}, id, 3000).catch(() => {})
    },
  }
  async function execute(call) {
    active.add(call.id)
    try {
      let reply
      try {
        if (call.operation === 'config.write') throw new Error('业务后端不能修改本机连接配置，请在桌面设置中保存。')
        reply = await runtime.perform(call, (asset) => send('/api/desktop/asset', { id: call.id, asset }, id, 60000, controller.signal))
      } catch (error) {
        reply = { id: call.id, ok: false, error: error.message, error_code: 'DESKTOP_RESULT_UNKNOWN', outcome_unknown: true }
      }
      if (!stopped) await send('/api/desktop/result', reply, id, 60000, controller.signal)
    } catch (error) {
      if (!stopped) { await connection.stop(); onDisconnected(error) }
    } finally { active.delete(call.id) }
  }
  async function poll() {
    while (!stopped) {
      try {
        const result = await send('/api/desktop/poll', {}, id, 25000, controller.signal)
        if (!Array.isArray(result.calls)) throw new Error('后端调用列表无效。')
        for (const call of result.calls) {
          if (stopped) break
          if (call.cancel) runtime.cancel(call.id)
          else void execute(call)
        }
      } catch (error) {
        if (!stopped) {
          await connection.stop()
          onDisconnected(error)
        }
      }
    }
  }
  void poll()
  return connection
}

// 一个 ERP 登录会话只维护一个连接。只重建传输通道，已经发出的业务操作不重放。
function createAccountConnection(baseUrl, runtime, { session, client, onChange }) {
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
        await previous.stop()
      }
    }
    if (stopped) throw new Error('桌面会话已结束。')
    if (current?.id) return
    const link = await connectDesktop(baseUrl, runtime, { session: auth, client,
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
