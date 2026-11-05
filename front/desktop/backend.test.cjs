const assert = require('node:assert/strict')
const { test } = require('node:test')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { backendEnvironment, startBackend } = require('./backend.cjs')

test('启动环境不继承开发端口、模型密钥及 Python 路径', () => {
  assert.deepEqual(backendEnvironment({ HOME: '/home/user', PATH: '/usr/bin',
    ERP_PORT: '5050', ERP_APP_DIR: '/dev', OPENAI_API_KEY: '测试秘密', PYTHONPATH: '/dev/.venv' }),
  { HOME: '/home/user', PATH: '/usr/bin' })
})

function fixture(t, body) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'champion-backend-test-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const executable = path.join(root, 'backend')
  fs.writeFileSync(executable, `#!${process.execPath}\n${body}\n`, { mode: 0o700 })
  return { root, executable }
}

test('只信任自有进程报告的随机端口，并在退出时关闭管道', async (t) => {
  const { root, executable } = fixture(t, `
    console.log('CHAMPION_ERP_READY ' + JSON.stringify({url:'http://127.0.0.1:45123/',pid:process.pid}));
    process.stdin.resume(); process.stdin.on('end', () => process.exit(0));
  `)
  const backend = startBackend(executable, path.join(root, 'runtime'))
  assert.equal(await backend.started, 'http://127.0.0.1:45123')
  await backend.stop()
  assert.throws(() => process.kill(backend.pid, 0))
})

test('后端启动失败不能连接开发服务', async (t) => {
  const { root, executable } = fixture(t, 'process.exit(7)')
  const backend = startBackend(executable, path.join(root, 'runtime'))
  await assert.rejects(backend.started, /启动失败/)
  await backend.stop()
})

test('启动超时会收尾子进程', async (t) => {
  const { root, executable } = fixture(t, `process.stdin.resume(); process.stdin.on('end', () => process.exit(0));`)
  const backend = startBackend(executable, path.join(root, 'runtime'), { timeoutMs: 100 })
  await assert.rejects(backend.started, /启动超时/)
  await backend.stop()
  assert.throws(() => process.kill(backend.pid, 0))
})

test('拒绝其他进程或非本地的就绪地址', async (t) => {
  const { root, executable } = fixture(t, `
    console.log('CHAMPION_ERP_READY ' + JSON.stringify({url:'http://example.com:5000/',pid:process.pid}));
    process.stdin.resume(); process.stdin.on('end', () => process.exit(0));
  `)
  const backend = startBackend(executable, path.join(root, 'runtime'))
  await assert.rejects(backend.started, /地址或进程身份无效/)
  await backend.stop()
})
