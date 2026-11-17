const { screen, WebContentsView } = require('electron')
const path = require('node:path')

const { readPanel } = require('./login-panel.cjs')

const barHeight = 54

function cropBounds(box, width, height, zoom) {
  if (!box) return null
  const left = Math.floor(box.x * zoom), top = Math.floor(box.y * zoom)
  const right = Math.ceil((box.x + box.width) * zoom), bottom = Math.ceil((box.y + box.height) * zoom)
  if (left < 0 || top < 0 || right > width || bottom > height || right <= left || bottom <= top) return null
  const w = right - left, h = bottom - top
  if (w >= width - 2 && h >= height - 2) return null
  return { x: left, y: top, w, h }
}

function mountLayout(win, view) {
  const contents = view.webContents
  const area = screen.getDisplayMatching(win.getBounds()).workArea
  const width = Math.min(1100, area.width - 24)
  const height = Math.min(800, area.height - barHeight - 60)
  const toolbar = new WebContentsView({ webPreferences: {
    preload: path.join(__dirname, 'toolbar-preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false,
  } })
  win.contentView.addChildView(toolbar)
  win.setResizable(false)
  let full = false, dragging = false, refreshing = false, ended = false, lastLabel = ''
  let crop = null
  function place(box) {
    crop = full ? null : cropBounds(box, width, height, contents.getZoomFactor())
    const rect = crop || { x: 0, y: 0, w: width, h: height }
    const [oldW, oldH] = win.getContentSize()
    if (oldW !== rect.w || oldH !== rect.h + barHeight) win.setContentSize(rect.w, rect.h + barHeight)
    // 网页尺寸保持不变，窗口边缘裁剪内容；最后加入的工具栏始终位于网页上方。
    view.setBounds({ x: -rect.x, y: barHeight - rect.y, width, height })
    toolbar.setBounds({ x: 0, y: 0, width: rect.w, height: barHeight })
    const url = contents.getURL()
    const host = crop ? box.host : url ? new URL(url).hostname : '加载中'
    const title = crop ? (box.reason === 'LOGIN' ? '1688 登录' : '1688 人机验证') : '完整页面'
    const label = `${title} · ${host}`
    if (label !== lastLabel) {
      toolbar.webContents.send('verification:status', { label, full })
      lastLabel = label
    }
  }
  async function refresh() {
    if (refreshing || ended || dragging) return
    refreshing = true
    try {
      const url = contents.getURL()
      let box = null
      if (!full && url && new URL(url).hostname === 'detail.1688.com') {
        box = await readPanel(contents)
      }
      if (!ended && !dragging && contents.getURL() === url) place(box)
    } catch {
      if (!ended && !dragging) place(null)
    } finally { refreshing = false }
  }
  function mouse(_event, input) {
    if (input.type === 'mouseDown' && crop) dragging = true
    if (input.type === 'mouseUp') dragging = false
  }
  contents.on('before-mouse-event', mouse)
  win.on('blur', () => { dragging = false })
  contents.on('did-navigate', () => { dragging = false; if (!ended) place(null) })
  toolbar.webContents.once('did-finish-load', () => { lastLabel = ''; void refresh() })
  function toggle() {
    full = !full
    lastLabel = ''
    if (full) place(null)
    else void refresh()
  }
  toolbar.webContents.ipc.on('verification:toggle', event => {
    if (event.senderFrame === toolbar.webContents.mainFrame) toggle()
  })
  toolbar.webContents.loadFile(path.join(__dirname, 'toolbar.html')).catch(() => {})
  place(null)
  const timer = setInterval(refresh, 350)
  win.once('closed', () => {
    ended = true
    clearInterval(timer)
    contents.removeListener('before-mouse-event', mouse)
    if (!toolbar.webContents.isDestroyed()) toolbar.webContents.close()
  })
}
module.exports = { mountLayout }
