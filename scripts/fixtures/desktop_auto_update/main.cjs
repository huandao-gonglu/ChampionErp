// 无窗口验收程序：使用真实 Electron 更新器，不连接 ERP 业务服务或钥匙串。
const { app, autoUpdater } = require('electron')
const fs = require('node:fs')
const path = require('node:path')
const settings = require('./settings.json')
const control = JSON.parse(fs.readFileSync(path.join(settings.root, 'control.json'), 'utf8'))
const { createDesktopUpdates } = require('./updates.cjs')
const { canUseNativeUpdates } = require('./update-signature.cjs')

const report = path.join(settings.root, `${control.scenario}.jsonl`)
function record(event, extra = {}) {
  fs.appendFileSync(report, JSON.stringify({ event, version: app.getVersion(), pid: process.pid, executable: process.execPath, ...extra }) + '\n')
}
app.setPath('userData', path.join(settings.root, 'user-data', control.scenario))
app.on('before-quit', () => record('quit'))
const watchdog = setTimeout(() => { record('timeout'); app.exit(4) }, 90000)
app.whenReady().then(async () => {
  app.setActivationPolicy('prohibited')
  record('launched')
  if (app.getVersion() === settings.targetVersion) {
    record('restarted')
    clearTimeout(watchdog)
    app.quit()
    return
  }
  autoUpdater.on('error', error => record('native-error', { message: error.message }))
  autoUpdater.on('update-downloaded', () => record('downloaded'))
  const updates = createDesktopUpdates({
    app, autoUpdater, origin: settings.publicOrigin,
    nativeSigned: () => canUseNativeUpdates(path.resolve(process.resourcesPath, '../..'), { identifier: settings.identifier }),
    dialog: { showMessageBox: async options => {
      record('dialog', { message: options.message, detail: options.detail, buttons: options.buttons })
      return { response: 0 }
    } },
    shell: { openPath: () => { throw new Error('验收不允许改为手工安装') }, showItemInFolder: () => { throw new Error('验收不允许改为手工安装') } },
    showProgress: () => ({ update() {}, close() {} }),
    // 仅将测试的 HTTPS 发行站点映射到本机服务；下载、摘要及原生更新逻辑均为正式代码。
    fetcher: (url, options) => fetch(new URL(new URL(url).pathname, settings.feedOrigin), options),
  })
  const result = await updates.startup()
  record('result', { result })
  if (result !== 'installing') { clearTimeout(watchdog); app.quit() }
}).catch(error => { record('fatal', { message: error.stack }); app.exit(3) })
