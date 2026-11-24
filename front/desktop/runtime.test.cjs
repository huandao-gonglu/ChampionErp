const assert = require('node:assert/strict')
const { test } = require('node:test')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { runtimeEnvironment, startRuntime } = require('./runtime.cjs')
const { normalizeBackendUrl, connectDesktop } = require('./connection.cjs')
const http = require('node:http')

test('本机执行器不继承业务配置、密钥或 Python 路径', () => {
  assert.deepEqual(runtimeEnvironment({ HOME: '/home/user', PATH: '/usr/bin', ERP_PORT: '5000', ERP_APP_DIR: '/server',
    OPENAI_API_KEY: 'secret', PYTHONPATH: '/dev', HTTPS_PROXY: 'http://proxy:7890' }),
  { HOME: '/home/user', PATH: '/usr/bin', HTTPS_PROXY: 'http://proxy:7890' })
})

function fixture(t, body) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'champion-runtime-test-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const executable = path.join(root, 'runtime')
  fs.writeFileSync(executable, `#!${process.execPath}\n${body}\n`, { mode: 0o700 })
  return { root, executable }
}

test('管道关联真实结果，完成素材上传后再交付回执，退出时关闭本次进程', async (t) => {
  const { root, executable } = fixture(t, `
    console.log(JSON.stringify({ready:true,pid:process.pid}));
    const lines = require('node:readline').createInterface({input:process.stdin});
    lines.on('line', line => { const call=JSON.parse(line);
      console.log(JSON.stringify({id:call.id,asset:{url:'https://image.test/a',data:'image'}}));
      console.log(JSON.stringify({id:call.id,ok:true,result:{value:call.params.value}}));
    });
    lines.on('close', () => process.exit(0));
  `)
  const runtime = startRuntime(executable, path.join(root, 'local'))
  t.after(() => runtime.stop())
  await runtime.started
  let uploaded = false
  const reply = await runtime.perform({ id: 'first', operation: 'config.read', params: { value: 123 }, timeout: 5 }, async () => {
    await new Promise(resolve => setTimeout(resolve, 10))
    uploaded = true
  })
  assert.equal(uploaded, true)
  assert.deepEqual(reply, { id: 'first', ok: true, result: { value: 123 } })
  await runtime.stop()
  assert.throws(() => process.kill(runtime.pid, 0))
})

test('执行器启动失败不启动其他后端', async (t) => {
  const { root, executable } = fixture(t, 'process.exit(7)')
  const runtime = startRuntime(executable, root)
  await assert.rejects(runtime.started, /退出/)
  await runtime.stop()
})

test('执行器启动超时会收尾进程', async (t) => {
  const { root, executable } = fixture(t, "process.stdin.resume();process.stdin.on('end',()=>process.exit(0))")
  const runtime = startRuntime(executable, root, { timeoutMs: 100 })
  await assert.rejects(runtime.started, /启动超时/)
  await runtime.stop()
})

test('统一后端地址只接受站点地址', () => {
  assert.equal(normalizeBackendUrl('https://erp.example.test/'), 'https://erp.example.test')
  for (const value of ['file:///tmp/erp', 'https://erp.test/api', 'https://name:secret@erp.test/', 'https://erp.test/?key=a']) {
    assert.throws(() => normalizeBackendUrl(value))
  }
})

test('转发与 Vue 页面生命周期无关，回传闪断仅幂等重试上传而不重放本机执行', async (t) => {
  let polls = 0, executions = 0, results = 0
  const server = http.createServer((request, response) => {
    request.resume()
    if (request.url === '/api/desktop/connect') response.end(JSON.stringify({ ok: true, connection_id: 'a'.repeat(40) }))
    else if (request.url === '/api/desktop/poll') {
      polls += 1
      if (polls === 1) response.end(JSON.stringify({ ok: true, calls: [{ id: 'owned', operation: 'browser.status', params: {}, timeout: 5 }] }))
      else if (results >= 4) {
        response.writeHead(409)
        response.end(JSON.stringify({ ok: false, error_code: 'DESKTOP_UNAVAILABLE', error: '连接已失效' }))
      } else {
        setTimeout(() => { if (!response.writableEnded) response.end(JSON.stringify({ ok: true, calls: [] })) }, 20)
      }
    } else if (request.url === '/api/desktop/result') {
      results += 1
      request.socket.destroy()
    } else response.end(JSON.stringify({ ok: true }))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => { server.closeAllConnections(); server.close() })
  let resolveDisconnected
  const disconnected = new Promise(resolve => { resolveDisconnected = resolve })
  const bridge = await connectDesktop(`http://127.0.0.1:${server.address().port}`, {
    async perform() { executions += 1; return { id: 'owned', ok: true, result: {} } }, cancel() {},
  }, { onDisconnected: resolveDisconnected, resultRetryDelaysMs: [10, 10, 10] })
  t.after(() => bridge.stop())
  await disconnected
  assert.equal(executions, 1)
  assert.equal(results, 4)
  assert.equal(bridge.id, '')
})
