const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const { createHash } = require('node:crypto')
const { EventEmitter } = require('node:events')
const { createDesktopUpdates, downloadInstaller, validate, compare } = require('./updates.cjs')

const bytes = Buffer.from('测试安装包')
const checksum = createHash('sha256').update(bytes).digest('hex')
const packageInfo = { file: 'installer.dmg', size: bytes.length, sha256: checksum }
const policy = { platform: 'darwin', arch: 'arm64', minimum: '1.2.0', version: '1.10.0', notes: '发布说明', installer: packageInfo }

async function setup(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'desktop-updates-'))
  t.after(() => fs.rm(directory, { recursive: true, force: true }))
  const state = { manifest: policy, answer: 1, messages: [], opened: [], revealed: [], native: false, fetched: [] }
  const native = new EventEmitter()
  native.setFeedURL = ({ url }) => { state.feed = url }
  native.checkForUpdates = () => queueMicrotask(() => native.emit('update-downloaded'))
  native.quitAndInstall = () => { state.installed = true }
  const create = () => createDesktopUpdates({
    platform: 'darwin', arch: 'arm64', origin: 'https://release.example', autoUpdater: native,
    nativeSigned: async () => state.native,
    app: { getPath: () => directory, getVersion: () => '1.2.8' },
    dialog: { showMessageBox: async value => { state.messages.push(value); return { response: state.answer } } },
    shell: { openPath: async value => { state.opened.push(value); return '' }, showItemInFolder: value => state.revealed.push(value) },
    showProgress: () => ({ update() {}, close() {} }),
    fetcher: async (url, options) => {
      state.fetched.push(url.href)
      assert.equal(options.redirect, 'error')
      if (url.pathname.endsWith('.dmg')) return new Response(bytes)
      if (!state.manifest) throw new Error('离线')
      return Response.json(state.manifest)
    },
  })
  return { create, state, directory }
}

test('可选更新可推迟，强制更新跨离线重启继续生效，撤回后恢复', async t => {
  const { create, state } = await setup(t)
  const client = create()
  assert.equal(await client.startup(), true)
  state.manifest = { ...policy, minimum: policy.version }
  await client.manual()
  assert.equal(client.blocksRequest('POST', '/api/save-draft'), true)
  assert.equal(client.blocksRequest('POST', '/api/v1/ai-chat/cancel'), false)
  assert.equal(client.blocksRequest('POST', '/api/publish-bus/reconcile'), false)
  assert.equal(client.blocksRequest('GET', '/api/tasks'), false)
  state.manifest = null
  assert.equal(await create().startup(), false)
  state.manifest = policy
  await client.manual()
  assert.equal(client.blocksRequest('POST', '/api/save-draft'), false)
})

test('未签名 Mac 启动时校验并打开安装包，在业务会话中只定位已下载文件', async t => {
  const { create, state } = await setup(t)
  state.answer = 0
  assert.equal(await create().startup(), false)
  assert.deepEqual(await fs.readFile(state.opened[0]), bytes)
  await create().manual()
  assert.equal(state.opened.length, 1)
  assert.equal(state.revealed.length, 1)
  assert.equal(state.fetched.at(-1), 'https://release.example/desktop-updates/darwin-arm64/1.10.0/installer.dmg')
})

test('已签名 Mac 仅在业务启动前交给 Electron 安装签名 ZIP', async t => {
  const { create, state } = await setup(t)
  state.native = true
  state.answer = 0
  state.manifest = { ...policy, macZip: { ...packageInfo, file: 'update.zip' } }
  assert.equal(await create().startup(), 'installing')
  assert.equal(state.feed, 'https://release.example/desktop-updates/darwin-arm64/1.10.0/native.json')
  assert.equal(state.installed, true)
  assert.equal(state.opened.length, 0)
})

test('拒绝错误架构、任意下载地址和包内容损坏；保留原有文件', async t => {
  const { directory } = await setup(t)
  assert.ok(compare('1.10.0', '1.2.9') > 0)
  assert.throws(() => validate(policy, 'darwin', 'x64'))
  assert.throws(() => validate({ ...policy, installer: { ...packageInfo, file: 'https://unsafe.test/app' } }, 'darwin', 'arm64'))
  const destination = path.join(directory, 'old.dmg')
  await fs.writeFile(destination, 'old')
  await assert.rejects(downloadInstaller('https://release.example/a', { ...packageInfo, sha256: '0'.repeat(64) },
    destination, new AbortController().signal, () => {}, async () => new Response(bytes)), /校验失败/)
  assert.equal(await fs.readFile(destination, 'utf8'), 'old')
  assert.deepEqual(await fs.readdir(directory), ['old.dmg'])
})
