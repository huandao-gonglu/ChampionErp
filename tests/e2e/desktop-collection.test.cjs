// 通过正式执行器跑完整采集流程。网站响应来自内存夹具，临时资料由父测试进程回收。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { randomUUID } = require('node:crypto')
const root = path.resolve(__dirname, '../..')
const offer = 'https://detail.1688.com/offer/900001.html'
const login = 'https://login.1688.com/member/signin'
const challenge = 'https://slide.1688.com/verify'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

function serveFixture(store) {
  const pages = new Map([
    [offer, `<h1>本地示例商品</h1><script type="application/json" id="buyer">{"buyerModel":{"userId":null}}</script>
      <button id="login"><od-text>登录查看全部规格</od-text></button>
      <script>
        const entry = document.querySelector('#login')
        const frame = document.createElement('iframe')
        entry.onclick = () => {
          frame.style = 'position:fixed;inset:0;width:100%;height:100%;border:0'
          frame.src = '${login}'; document.body.append(frame)
        }
        onmessage = e => {
          if (e.source !== frame.contentWindow) return
          if (e.origin === new URL('${login}').origin) {
            if (e.data === 'close') frame.remove()
            if (e.data === 'challenge') {
              frame.style = 'position:fixed;left:70px;top:80px;width:390px;height:210px;border:0'
              frame.src = '${challenge}'
            }
          }
          if (e.origin === new URL('${challenge}').origin) {
            if (e.data === 'expand') frame.style.height = '295px'
            if (e.data === 'done') {
              frame.remove(); entry.remove()
              document.querySelector('#buyer').textContent = JSON.stringify({buyerModel:{userId:42}})
            }
          }
        }
      </script>`],
    [login, `<div style="position:fixed;inset:0;display:grid;place-items:center">
      <div class="login-card" style="background:white;width:min(752px,90vw);height:min(458px,80vh)">
      短信登录<input type="tel" disabled><button class="close">关闭</button><button id="next">人机验证</button></div></div>
      <script>document.querySelector('.close').onclick=()=>parent.postMessage('close','https://detail.1688.com');
      document.querySelector('#next').onclick=()=>parent.postMessage('challenge','https://detail.1688.com')</script>`],
    [challenge, `<button id="expand">展开</button><button id="finish">完成测试</button>
      <script>document.querySelector('#expand').onclick=()=>parent.postMessage('expand','https://detail.1688.com');
      document.querySelector('#finish').onclick=()=>parent.postMessage('done','https://detail.1688.com')</script>`],
  ])
  store.protocol.handle('https', req => new Response(pages.has(req.url) ? '<!doctype html><meta charset="utf-8">' + pages.get(req.url) : null, {
    status: pages.has(req.url) ? 200 : 404, headers: { 'content-type': 'text/html; charset=utf-8' },
  }))
}

async function checkCollection(runtime, layout) {
  const cropped = () => layout.win && layout.win.getContentSize()[0] < layout.view.getBounds().width
  const toolbar = () => layout.win.contentView.children.at(-1).webContents
  async function until(condition) {
    const deadline = Date.now() + 15000
    while (!await condition()) {
      if (Date.now() > deadline) throw new Error('等待测试页面状态变化超时')
      await delay(60)
    }
  }
  // 从采集队列使用的执行器入口发起请求，验证跨调用保留原商品页。
  async function perform(operation, params) {
    const reply = await runtime.perform({ id: randomUUID(), timeout: 30, operation, params })
    assert.equal(reply.ok, true, reply.error?.message || '本地采集调用失败')
    return reply.result
  }
  const startCollection = () => perform('collect.read', { product_url: offer, open_url: true })
  const first = await startCollection()
  const pageId = first.snapshot.browser_tab_id
  assert.ok(pageId)
  assert.deepEqual(first.snapshot, { url: offer, browser_tab_id: pageId, verification_reason: 'LOGIN' })
  const verify = () => perform('collect.verify', { browser_tab_id: pageId, source_url: offer })
  assert.equal((await verify()).status, 'waiting_verification')
  const showLogin = () => layout.contents.executeJavaScript("document.querySelector('#login').click()")
  const loginFrame = () => layout.contents.mainFrame.frames.find(frame => frame.url.startsWith('https://login.1688.com/'))
  await until(() => cropped())
  // 首次挂载时网页从后台尺寸调整到显示器工作区，等裁剪跟上当前页面尺寸。
  await until(async () => {
    const card = await loginFrame().executeJavaScript("document.querySelector('.login-card').getBoundingClientRect().toJSON()")
    const viewport = layout.view.getBounds()
    const [width, height] = layout.win.getContentSize()
    return viewport.x === -Math.floor(card.left) && viewport.y === 54 - Math.floor(card.top) &&
      width === Math.ceil(card.right) - Math.floor(card.left) && height === 54 + Math.ceil(card.bottom) - Math.floor(card.top)
  })
  await loginFrame().executeJavaScript("document.querySelector('.close').click()")
  await until(() => !cropped())
  assert.equal((await verify()).status, 'waiting_verification')
  assert.equal(cropped(), false)
  // 商品页也可能直接创建弹层，没有 iframe、role 或带 login 的类名。
  const inline = await layout.contents.executeJavaScript(`(() => {
    const layer = document.createElement('div')
    layer.style = 'position:fixed;inset:0;display:grid;place-items:center;background:#0006'
    layer.innerHTML = '<div style="width:620px;height:360px;background:white"><input type="tel"><p>短信登录</p><aside>扫码登录</aside></div>'
    document.body.append(layer)
    try { return ${require('../../front/desktop/verification/login-panel.cjs').panelExpression}.box }
    finally { layer.remove() }
  })()`)
  assert.equal(inline.width, 620)
  assert.equal(inline.height, 360)
  const clippedNavigation = await layout.contents.executeJavaScript(`(() => {
    const nav = document.createElement('button')
    nav.textContent = '登录'
    nav.style = 'position:absolute;left:150vw;top:24px'
    const original = document.querySelector('#login')
    original.hidden = true
    document.body.append(nav)
    let clicks = 0
    nav.onclick = () => clicks++
    try {
      (${require('../../front/desktop/verification/login-entry.cjs').openLoginEntry})(location.origin + location.pathname, null)
      return clicks
    } finally { nav.remove(); original.hidden = false; scrollTo(0, 0) }
  })()`)
  assert.equal(clippedNavigation, 1)
  await showLogin()
  await until(() => cropped())
  await loginFrame().executeJavaScript("document.querySelector('#next').click()")
  await until(() => layout.win.getContentSize()[0] === 390)
  const current = layout.contents.id
  const original = layout.view.getBounds()
  assert.ok(original.x < 0 && original.y < 0)
  await toolbar().executeJavaScript("document.querySelector('button').click()")
  await until(() => !cropped())
  assert.equal(layout.view.getBounds().width, original.width)
  await toolbar().executeJavaScript("document.querySelector('button').click()")
  await until(() => cropped())
  const height = layout.win.getContentSize()[1]
  const frame = layout.contents.mainFrame.frames.find(frame => frame.url.startsWith('https://slide.1688.com/'))
  await frame.executeJavaScript("document.querySelector('#expand').click()")
  await until(() => layout.win.getContentSize()[1] > height)
  assert.equal(layout.contents.id, current)
  await frame.executeJavaScript("document.querySelector('#finish').click()")
  await until(() => !cropped())
  assert.equal((await verify()).status, 'ready')
  assert.equal(layout.win.isVisible(), false)
  const saved = await perform('collect.read', { product_url: offer, browser_tab_id: pageId })
  assert.equal(saved.snapshot.browser_tab_id, pageId)
  assert.equal(saved.snapshot.final_url, offer)
  assert.match(saved.snapshot.text, /本地示例商品/)
  assert.equal(saved.browserStatus.tabs_count, 0)
  const cancelled = await startCollection()
  await until(() => cropped())
  layout.win.close()
  const afterClose = await perform('collect.verify', { browser_tab_id: cancelled.snapshot.browser_tab_id, source_url: offer })
  assert.equal(afterClose.status, 'unavailable')
}

async function electronTest() {
  const { app, BaseWindow, session, WebContentsView } = require('electron')
  const { createEmbeddedBrowser } = require('../../front/desktop/embedded-browser.cjs')
  const { mountLayout } = require('../../front/desktop/verification/layout.cjs')
  const { startRuntime } = require('../../front/desktop/runtime.cjs')
  const directory = process.argv[2]
  app.setPath('userData', directory)
  await app.whenReady()
  const store = session.fromPartition('collection-test')
  serveFixture(store)
  const layout = {}
  const account = () => 'collection-test'
  const browser = createEmbeddedBrowser({ BaseWindow, WebContentsView, account, parent: () => undefined,
    session: { fromPartition: () => store }, layoutVerificationWindow(win, view) {
      mountLayout(win, view)
      Object.assign(layout, { win, view, contents: view.webContents })
    },
  })
  const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const runtime = startRuntime(python, directory, { cwd: root, args: ['-m', 'desktop_runtime'], browser, browserOwner: account })
  app.on('window-all-closed', () => {})
  try {
    await runtime.started
    await checkCollection(runtime, layout)
  } finally {
    await runtime.stop()
    await browser.flush()
  }
}

if (process.versions.electron) {
  const { app } = require('electron')
  electronTest().then(() => app.quit(), error => { console.error(error); app.exit(1) })
} else {
  const { test } = require('node:test')
  const { execFile } = require('node:child_process')
  const run = require('node:util').promisify(execFile)
  test('1688 登录和人机验证完成后恢复同一商品，手动关窗终止等待', async () => {
    const electron = require(require.resolve('electron', { paths: [path.join(root, 'front')] }))
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'erp-collection-test-'))
    const env = { ...process.env }
    delete env.ELECTRON_RUN_AS_NODE
    try {
      await run(electron, [__filename, directory], { cwd: root, env, timeout: 90000 })
    } finally {
      fs.rmSync(directory, { recursive: true, force: true })
    }
  })
}
