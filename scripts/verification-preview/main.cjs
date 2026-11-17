const { app, BaseWindow, BrowserWindow, ipcMain, session, WebContentsView } = require('electron')
const { execFileSync } = require('node:child_process')
const { randomUUID } = require('node:crypto')
const path = require('node:path')
const { createEmbeddedBrowser } = require('../../front/desktop/embedded-browser.cjs')
const { startRuntime } = require('../../front/desktop/runtime.cjs')
const { offer, installDemo } = require('./demo.cjs')
const { mountLayout } = require('./layout.cjs')

const root = path.join(__dirname, '../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const selfTest = process.argv.includes('--self-test')
app.setName('ERP Verification Preview')
app.setPath('userData', path.join(app.getPath('appData'), 'Champion ERP Verification Preview'))
let launcher, browser, runtime, generation = 0, layout, demo = true, quitting = false
let lastStatus = ''
function status(label) {
  lastStatus = label
  if (launcher && !launcher.isDestroyed()) launcher.webContents.send('preview:status', { label })
}
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

async function open(input) {
  const url = input ? new URL(input.trim()) : new URL(offer)
  if (url.origin !== 'https://detail.1688.com' || !/^\/offer\/\d+\.html$/.test(url.pathname) ||
      url.username || url.password) throw new Error('请输入 1688 商品详情链接。')
  const run = ++generation
  const active = () => generation === run
  browser.reset()
  layout = undefined
  demo = !input
  const owner = demo ? 'preview-demo' : 'preview-1688'
  status(demo ? '正在打开本地滑块…' : '正在后台检查商品页…')
  const call = { id: randomUUID(), active }
  async function handle(action, data) { return browser.handle(action, data, owner, call) }
  async function check() {
    try {
      const id = await handle('open', { kind: '1688', url: url.href })
      if (!active()) return
      while (active()) {
        const reply = await runtime.perform({ id: randomUUID(), timeout: 18, operation: 'collect.verify',
          params: { browser_tab_id: id, source_url: url.href } })
        if (!active()) return
        if (!reply.ok) throw new Error('检查失败')
        const state = reply.result.status
        if (state === 'ready') {
          await handle('close', { page: id })
          if (active()) status(demo ? '示例已完成，已返回原商品并收起窗口。' : '原商品已确认登录且通过验证，测试结束。')
          return
        }
        if (state === 'unavailable') {
          status('原商品页已关闭或已离开目标商品，本次测试未完成。')
          browser.reset()
          return
        }
        if (state === 'waiting_verification' && !layout) {
          await handle('show', { page: id })
          if (active()) status('请在验证窗口操作，可随时切换局部和完整页面。')
        }
        await delay(650)
      }
    } catch {
      if (active()) {
        browser.reset()
        status('页面已关闭或加载失败。请检查网络与商品链接后重新打开。')
      }
    }
  }
  void check()
}

async function ready() {
  const expression = execFileSync(python, ['-c', 'from desktop_runtime.collection import VERIFICATION_FRAME_DETAILS; print(VERIFICATION_FRAME_DETAILS)'],
    { cwd: root, encoding: 'utf8' }).trim()
  const fixture = session.fromPartition('verification-preview-fixture')
  installDemo(fixture)
  browser = createEmbeddedBrowser({ BaseWindow, WebContentsView,
    session: { fromPartition: key => demo ? fixture : session.fromPartition(key) },
    account: () => demo ? 'preview-demo' : 'preview-1688', parent: () => launcher,
    layoutVerificationWindow: (win, view) => { layout = mountLayout(win, view, expression, demo) },
  })
  runtime = startRuntime(python, path.join(app.getPath('userData'), 'runtime'), { cwd: root, args: ['-m', 'desktop_runtime'],
    browser, browserOwner: () => demo ? 'preview-demo' : 'preview-1688' })
  await runtime.started
  launcher = new BrowserWindow({ width: 560, height: 440, show: false, webPreferences: {
    preload: path.join(__dirname, 'preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false,
  } })
  ipcMain.handle('preview:start', (event, input) => {
    if (event.sender !== launcher.webContents || event.senderFrame !== launcher.webContents.mainFrame || typeof input !== 'string') return
    return open(input)
  })
  ipcMain.handle('preview:toggle', event => {
    if (layout && event.sender === layout.toolbar && event.senderFrame === layout.toolbar.mainFrame) layout.toggle()
  })
  launcher.once('closed', () => { generation++; browser.reset(); app.quit() })
  await launcher.loadFile(path.join(__dirname, 'panel.html'))
  launcher.show()
  await open('')
  if (selfTest) await testFixture()
}
async function testFixture() {
  const assert = require('node:assert/strict')
  async function until(condition) {
    const deadline = Date.now() + 15000
    while (!condition()) {
      if (Date.now() > deadline) throw new Error('验证超时：' + lastStatus)
      await delay(60)
    }
  }
  await until(() => layout?.cropped)
  const current = layout.contents.id
  const original = layout.view.getBounds()
  assert.ok(original.x < 0 && original.y < 0)
  layout.toggle()
  await until(() => !layout.cropped)
  assert.equal(layout.view.getBounds().width, original.width)
  layout.toggle()
  await until(() => layout.cropped)
  const height = layout.win.getContentSize()[1]
  const frame = layout.contents.mainFrame.frames[0]
  await frame.executeJavaScript("document.querySelector('button').click()")
  await until(() => layout.win.getContentSize()[1] > height)
  assert.equal(layout.contents.id, current)
  await frame.executeJavaScript("const s=document.querySelector('input');s.value='100';s.dispatchEvent(new Event('change'))")
  await until(() => lastStatus.startsWith('示例已完成'))
  console.log('通过：裁剪、完整原页、弹窗变高、验证通过后收起')
  await open('')
  await until(() => layout?.cropped)
  layout.win.close()
  await until(() => lastStatus.includes('未完成'))
  console.log('通过：关闭弹窗没有被当作验证成功')
  app.quit()
}
app.on('before-quit', event => {
  if (quitting || !runtime) return
  event.preventDefault()
  quitting = true
  generation++
  browser.reset()
  runtime.stop().then(() => browser.flush()).finally(() => app.quit())
})
app.whenReady().then(ready).catch(error => { console.error(error); app.exit(1) })
