// 桌面主进程只向发行站点读取当前系统和架构的清单，不携带账号会话。
const fs = require('node:fs/promises')
const path = require('node:path')
const { createHash } = require('node:crypto')
const { createWriteStream } = require('node:fs')
const { Readable, Transform } = require('node:stream')
const { pipeline } = require('node:stream/promises')
const { createNativeUpdateFeed } = require('./native-update-feed.cjs')

const SUFFIXES = { darwin: '.dmg', win32: '.exe', linux: '.AppImage' }
const MAX_MANIFEST_BYTES = 16 * 1024
const CONTINUATION_PATHS = new Set([
  '/api/account/logout', '/api/load-product', '/api/load-draft', '/api/draft-images/state',
  '/api/v1/ai-chat/cancel', '/api/tasks/check', '/api/publish-bus/reconcile', '/api/online-products/reconcile',
  '/api/wechat/verify', '/api/internal/newapi/session', '/api/internal/newapi/user',
])

function parts(version) {
  if (typeof version !== 'string' || !/^(0|[1-9]\d{0,7})(\.(0|[1-9]\d{0,7})){2}$/.test(version)) {
    throw new Error('桌面版本号无效。')
  }
  return version.split('.').map(Number)
}

function compare(left, right) {
  const a = parts(left), b = parts(right)
  return a[0] - b[0] || a[1] - b[1] || a[2] - b[2]
}

function validate(manifest, platform, arch) {
  const checkAsset = (asset, filename) => asset?.file === filename &&
    /^[0-9a-f]{64}$/.test(asset.sha256) && Number.isSafeInteger(asset.size) && asset.size > 0 && asset.size < 4 * 1024 ** 3
  if (!manifest || manifest.platform !== platform || manifest.arch !== arch ||
      !Object.hasOwn(SUFFIXES, platform) || !['x64', 'arm64'].includes(arch) ||
      compare(manifest.minimum, manifest.version) > 0 ||
      typeof manifest.notes !== 'string' || manifest.notes.length > 2000 ||
      !checkAsset(manifest.installer, 'installer' + SUFFIXES[platform]) ||
      (manifest.macZip && (platform !== 'darwin' || !checkAsset(manifest.macZip, 'update.zip')))) {
    throw new Error('更新清单与本机不匹配或安装包信息无效。')
  }
  return manifest
}

async function readManifest(url, fetcher) {
  const response = await fetcher(url, { signal: AbortSignal.timeout(6000), redirect: 'error', cache: 'no-store', credentials: 'omit' })
  if (response.status === 404) return null
  if (!response.ok) throw new Error('版本服务暂时不可用。')
  const chunks = []
  let size = 0
  for await (const chunk of response.body) {
    size += chunk.length
    if (size > MAX_MANIFEST_BYTES) throw new Error('更新清单过大。')
    chunks.push(chunk)
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

async function downloadInstaller(url, asset, destination, signal, onProgress, fetcher) {
  const response = await fetcher(url, { redirect: 'error', credentials: 'omit', signal })
  if (!response.ok || !response.body) throw new Error('安装包下载失败。')
  const hash = createHash('sha256')
  let size = 0
  const verify = new Transform({
    transform(chunk, _, callback) {
      size += chunk.length
      if (size > asset.size) return callback(new Error('安装包大小与发布清单不符。'))
      hash.update(chunk)
      onProgress(size / asset.size)
      callback(null, chunk)
    },
  })
  const tmp = await fs.mkdtemp(path.join(path.dirname(destination), '.download-'))
  const partial = path.join(tmp, 'package')
  try {
    await pipeline(Readable.fromWeb(response.body), verify, createWriteStream(partial, { flags: 'wx', mode: 0o600 }), { signal })
    if (size !== asset.size || hash.digest('hex') !== asset.sha256) throw new Error('安装包校验失败，请重新下载。')
    await fs.rename(partial, destination)
  } finally {
    await fs.rm(tmp, { recursive: true, force: true })
  }
}

function createDesktopUpdates({ app, dialog, shell, autoUpdater, origin, nativeSigned, platform = process.platform,
  arch = process.arch, fetcher = fetch, showProgress, parentWindow = () => null }) {
  const url = new URL(origin)
  if (url.protocol !== 'https:' || url.origin !== origin) throw new Error('桌面更新地址必须是 HTTPS 站点。')
  const base = new URL('/desktop-updates/' + platform + '-' + arch + '/', url)
  const directory = path.join(app.getPath('userData'), 'updates', platform + '-' + arch)
  const cache = path.join(directory, 'latest.json')
  const current = app.getVersion()
  let busy, announced = '', timer, belowMinimum = false
  function message(options) {
    const parent = parentWindow()
    const config = { type: 'info', title: '桌面更新', noLink: true, ...options }
    return parent && !parent.isDestroyed() ? dialog.showMessageBox(parent, config) : dialog.showMessageBox(config)
  }

  async function policy(manual) {
    let previous
    try { previous = validate(JSON.parse(await fs.readFile(cache, 'utf8')), platform, arch) }
    catch { /* 首次安装或缓存不可用时向发行服务获取策略。 */ }
    try {
      const raw = await readManifest(new URL('latest.json', base), fetcher)
      if (!raw) return previous
      const manifest = validate(raw, platform, arch)
      try {
        await fs.mkdir(directory, { recursive: true, mode: 0o700 })
        await fs.writeFile(cache + '.tmp', JSON.stringify(manifest), { mode: 0o600 })
        await fs.rename(cache + '.tmp', cache)
      } catch (error) {
        // 磁盘暂不可写时，当前会话仍执行刚取得的最低版本。
        process.emitWarning('桌面版本策略缓存写入失败：' + error.code)
      }
      return manifest
    } catch (error) {
      if (manual && !previous) throw error
      if (manual) await message({ message: '未能刷新版本信息，将显示上次取得的版本。' })
      return previous
    }
  }

  async function installNative(manifest, versionUrl) {
    const archive = await getPackage(manifest, versionUrl, manifest.macZip, '.zip')
    const feed = await createNativeUpdateFeed(archive, manifest.macZip, manifest.version)
    try {
      await new Promise((resolve, reject) => {
        function cleanup() {
          autoUpdater.removeListener('error', failed)
          autoUpdater.removeListener('update-not-available', unavailable)
          autoUpdater.removeListener('update-downloaded', done)
        }
        function failed(error) { cleanup(); reject(error) }
        function done() { cleanup(); resolve() }
        function unavailable() { failed(new Error('签名更新包尚不可用。')) }
        autoUpdater.once('error', failed)
        autoUpdater.once('update-not-available', unavailable)
        autoUpdater.once('update-downloaded', done)
        try { autoUpdater.setFeedURL({ url: feed.url }); autoUpdater.checkForUpdates() } catch (error) { failed(error) }
      })
    } finally { await feed.close() }
    autoUpdater.quitAndInstall()
  }

  async function getPackage(manifest, versionUrl, asset = manifest.installer, suffix = SUFFIXES[platform]) {
    await fs.mkdir(directory, { recursive: true, mode: 0o700 })
    const destination = path.join(directory, 'ERP-' + manifest.version + suffix)
    const task = new AbortController()
    const progress = showProgress(() => task.abort())
    try {
      await downloadInstaller(new URL(asset.file, versionUrl), asset, destination,
        AbortSignal.any([task.signal, AbortSignal.timeout(20 * 60 * 1000)]), progress.update, fetcher)
      if (platform === 'linux') await fs.chmod(destination, 0o700)
      return destination
    } finally {
      progress.close()
    }
  }

  async function check({ startup = false, manual = false } = {}) {
    const manifest = await policy(manual)
    belowMinimum = !!manifest && compare(current, manifest.minimum) < 0
    if (!manifest || compare(manifest.version, current) <= 0) {
      if (manual) await message({ message: '当前没有可用的桌面更新。', detail: '当前版本 ' + current })
      return true
    }
    const required = compare(current, manifest.minimum) < 0
    const key = manifest.version + '/' + manifest.minimum
    if (!startup && !manual && announced === key) return true
    announced = key
    const versionUrl = new URL(manifest.version + '/', base)
    const native = startup && manifest.macZip && await nativeSigned()
    const dismiss = required && startup ? '退出应用' : '稍后'
    const buttons = native ? ['安装并重启', '下载安装包', dismiss] : [startup ? '下载并安装' : '下载安装包', dismiss]
    const response = await message({ message: '发现桌面新版 ' + manifest.version,
      detail: [required ? '最低可用版本为 ' + manifest.minimum + '。' : '本次为可选更新。',
        !startup && (required ? '新的业务提交已暂停。请等当前工作结束后安装。' : '当前工作可继续，工作结束后可安装新版。'),
        manifest.notes].filter(Boolean).join('\n'), buttons, cancelId: buttons.length - 1, defaultId: 0 })
    if (response.response < 0 || response.response === buttons.length - 1) return !(startup && required)
    try {
      if (native && response.response === 0) {
        await installNative(manifest, versionUrl)
        return 'installing'
      }
      const destination = await getPackage(manifest, versionUrl)
      if (startup && platform !== 'linux') {
        const error = await shell.openPath(destination)
        if (error) throw new Error('无法打开安装包，请从更新目录手动安装。')
        return false
      }
      shell.showItemInFolder(destination)
      return !(startup && required)
    } catch (error) {
      await message({ type: 'warning', message: '本次更新未完成。', detail: error.message })
      return !(startup && required)
    }
  }

  function run(options) {
    if (!busy) busy = check(options).finally(() => { busy = undefined })
    return busy
  }
  return {
    blocksRequest(method, pathname) {
      if (!belowMinimum || method !== 'POST' || !pathname.startsWith('/api/')) return false
      return !CONTINUATION_PATHS.has(pathname)
    },
    startup: () => run({ startup: true }),
    manual: () => run({ manual: true }).catch(error => message({ type: 'warning', message: '检查更新失败', detail: error.message })),
    watch() {
      timer = setInterval(() => { void run({}).catch(() => {}) }, 30 * 60 * 1000)
      timer.unref()
    },
    stop() { clearInterval(timer) },
  }
}

module.exports = { createDesktopUpdates, validate, compare, downloadInstaller }
