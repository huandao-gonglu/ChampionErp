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
const zipBytes = Buffer.from('测试自动更新包')
const macZip = { file: 'update.zip', size: zipBytes.length, sha256: createHash('sha256').update(zipBytes).digest('hex') }
const zipUrl = 'https://release.example/desktop-updates/darwin-arm64/1.10.0/update.zip'
const installerUrl = 'https://release.example/desktop-updates/darwin-arm64/1.10.0/installer.dmg'

async function setup(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'desktop-updates-'))
  t.after(() => fs.rm(directory, { recursive: true, force: true }))
  const state = { manifest: policy, answer: 1, messages: [], opened: [], revealed: [], native: false, fetched: [], downloads: 0, downloadKinds: [] }
  const native = new EventEmitter()
  native.setFeedURL = ({ url }) => { state.feed = url }
  native.checkForUpdates = async () => {
    try {
      const response = await fetch(state.feed)
      state.nativeFeed = await response.json()
      state.nativeBytes = Buffer.from(await (await fetch(state.nativeFeed.url)).arrayBuffer())
      if (state.nativeError) native.emit('error', new Error(state.nativeError))
      else native.emit('update-downloaded')
    } catch (error) { native.emit('error', error) }
  }
  native.quitAndInstall = () => { state.installed = true }
  const create = () => createDesktopUpdates({
    platform: 'darwin', arch: 'arm64', origin: 'https://release.example', autoUpdater: native,
    nativeSigned: async () => state.native,
    app: { getPath: () => directory, getVersion: () => '1.2.8' },
    dialog: { showMessageBox: async value => { state.messages.push(value); return { response: state.answer } } },
    shell: { openPath: async value => { state.opened.push(value); return '' }, showItemInFolder: value => state.revealed.push(value) },
    showProgress: (_cancel, kind) => { state.downloads += 1; state.downloadKinds.push(kind); return { update() {}, close() {} } },
    fetcher: async (url, options) => {
      state.fetched.push(url.href)
      assert.equal(options.redirect, 'error')
      if (!state.manifest) throw new Error('离线')
      if (url.href === zipUrl) return new Response(zipBytes)
      if (url.href === installerUrl) return new Response(bytes)
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
  assert.equal(state.revealed[0], state.opened[0])
  assert.equal(state.fetched.filter(url => url.endsWith('.dmg')).length, 1)
  assert.deepEqual(state.downloadKinds, ['installer'])
})

test('已签名 Mac 启动前校验 ZIP，再由本机来源交给 Electron 安装，不并排暴露 DMG 下载', async t => {
  const { create, state } = await setup(t)
  state.native = true
  state.answer = 0
  state.manifest = { ...policy, macZip }
  assert.equal(await create().startup(), 'installing')
  assert.deepEqual(state.messages[0].buttons, ['下载并安装', '稍后'])
  assert.deepEqual(state.downloadKinds, ['update'])
  assert.match(state.feed, /^http:\/\/127\.0\.0\.1:\d+\/[a-f0-9]{48}\/feed$/)
  assert.deepEqual(state.nativeBytes, zipBytes)
  assert.equal(state.nativeFeed.sha256, macZip.sha256)
  assert.equal(state.fetched.at(-1), zipUrl)
  assert.equal(state.installed, true)
  assert.equal(state.opened.length, 0)
  await assert.rejects(fetch(state.feed))
})

test('业务运行中下载 ZIP，不暴露 DMG 按钮，新进程离线启动直接安装且不再次下载', async t => {
  const { create, state } = await setup(t)
  state.native = true
  state.answer = 0
  state.manifest = { ...policy, macZip }
  await create().manual()
  assert.deepEqual(state.messages[0].buttons, ['下载更新', '稍后'])
  assert.deepEqual(state.downloadKinds, ['update'])
  assert.equal(state.messages.at(-1).message, '更新已下载。')
  assert.equal(state.feed, undefined)
  assert.deepEqual(state.revealed, [])

  state.answer = 0
  await create().manual()
  assert.equal(state.messages.at(-1).message, '桌面新版 1.10.0 已下载')
  assert.deepEqual(state.messages.at(-1).buttons, ['知道了'])
  assert.equal(state.messages.at(-1).defaultId, 0)
  assert.equal(state.messages.at(-1).cancelId, 0)
  assert.deepEqual(state.revealed, [])
  assert.equal(state.fetched.filter(url => url.endsWith('.dmg')).length, 0)

  state.answer = 0
  state.manifest = null
  assert.equal(await create().startup(), 'installing')
  assert.deepEqual(state.messages.at(-1).buttons, ['安装并重启', '稍后'])
  assert.deepEqual(state.fetched.filter(url => /\.(zip|dmg)$/.test(url)), [zipUrl])
  assert.equal(state.downloads, 1)
  assert.deepEqual(state.nativeBytes, zipBytes)
  assert.equal(state.installed, true)
})

test('缓存 ZIP 大小正确但摘要不符时，启动提示下载并安装', async t => {
  const { create, state, directory } = await setup(t)
  state.native = true
  state.answer = 0
  state.manifest = { ...policy, macZip }
  const cacheDirectory = path.join(directory, 'updates', 'darwin-arm64')
  await fs.mkdir(cacheDirectory, { recursive: true })
  const archive = path.join(cacheDirectory, 'ERP-1.10.0.zip')
  await fs.writeFile(archive, Buffer.alloc(macZip.size))
  assert.equal(await create().startup(), 'installing')
  assert.equal(state.messages[0].buttons[0], '下载并安装')
  assert.equal(state.downloads, 1)
  assert.deepEqual(await fs.readFile(archive), zipBytes)
  assert.deepEqual(state.nativeBytes, zipBytes)
})

test('自动更新拒绝摘要错误或原生签名拒绝，不退出或静默改为安装其他包', async t => {
  const { create, state } = await setup(t)
  state.native = true
  state.answer = 0
  state.manifest = { ...policy, macZip: { ...macZip, sha256: '0'.repeat(64) } }
  assert.equal(await create().startup(), true)
  assert.match(state.messages.at(-1).detail, /校验失败/)
  assert.equal(state.feed, undefined)
  state.manifest.macZip.sha256 = macZip.sha256
  state.nativeError = '更新包签名不匹配'
  assert.equal(await create().startup(), true)
  assert.match(state.messages.at(-1).detail, /签名不匹配/)
  assert.equal(state.installed, undefined)
  assert.equal(state.opened.length, 0)
  await assert.rejects(fetch(state.feed))
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
