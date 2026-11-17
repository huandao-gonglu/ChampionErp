const { screen, WebContentsView } = require('electron')
const path = require('node:path')

const barHeight = 54
const margin = 14

function cropBounds(box, width, height, zoom) {
  if (!box) return null
  const left = Math.floor(box.x * zoom), top = Math.floor(box.y * zoom)
  const right = Math.ceil((box.x + box.width) * zoom), bottom = Math.ceil((box.y + box.height) * zoom)
  if (left < 0 || top < 0 || right > width || bottom > height || right <= left || bottom <= top) return null
  const w = Math.min(width, Math.max(440, right - left + 2 * margin))
  const h = Math.min(height, bottom - top + 2 * margin)
  if (w * h > width * height * 0.83) return null
  const x = Math.max(0, Math.min(width - w, Math.floor((left + right - w) / 2)))
  const y = Math.max(0, Math.min(height - h, top - margin))
  return { x, y, w, h }
}

function mountLayout(win, view, frameExpression, demo) {
  const contents = view.webContents
  const area = screen.getDisplayMatching(win.getBounds()).workArea
  const width = Math.min(1100, area.width - 24)
  const height = Math.min(800, area.height - barHeight - 60)
  const toolbar = new WebContentsView({ webPreferences: {
    preload: path.join(__dirname, 'preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false,
  } })
  win.contentView.addChildView(toolbar)
  win.setResizable(false)
  let full = false, dragging = false, refreshing = false, ended = false, lastLabel = ''
  let crop = null, announced = false
  function place(box) {
    crop = full ? null : cropBounds(box, width, height, contents.getZoomFactor())
    const rect = crop || { x: 0, y: 0, w: width, h: height }
    const [oldW, oldH] = win.getContentSize()
    if (oldW !== rect.w || oldH !== rect.h + barHeight) win.setContentSize(rect.w, rect.h + barHeight)
    // 网页尺寸保持不变，窗口边缘裁剪内容；最后加入的工具栏始终位于网页上方。
    view.setBounds({ x: -rect.x, y: barHeight - rect.y, width, height })
    toolbar.setBounds({ x: 0, y: 0, width: rect.w, height: barHeight })
    const url = contents.getURL()
    const origin = demo ? '本地示例' : url ? new URL(url).hostname : '加载中'
    const label = (crop ? '局部显示' : '完整页面') + ' · ' + origin + (!demo && crop ? ' → ' + box.host : '')
    if (label !== lastLabel) {
      if (demo) win.setTitle('1688 验证窗口测试 · 本地滑块示例')
      toolbar.webContents.send('preview:status', { label, full })
      if (demo && crop && !announced) {
        announced = true
        console.log('本地滑块窗口已就绪')
      }
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
        box = await contents.executeJavaScript(frameExpression)
      }
      if (!ended && !dragging && contents.getURL() === url) place(box)
    } catch {
      if (!ended && !dragging) place(null)
    } finally { refreshing = false }
  }
  function mouse(_event, input) {
    if (input.type === 'mouseDown') dragging = true
    if (input.type === 'mouseUp') dragging = false
  }
  contents.on('before-mouse-event', mouse)
  win.on('blur', () => { dragging = false })
  contents.on('did-navigate', () => { dragging = false; if (!ended) place(null) })
  toolbar.webContents.once('did-finish-load', () => { lastLabel = ''; void refresh() })
  toolbar.webContents.loadFile(path.join(__dirname, 'panel.html'), { hash: 'toolbar' }).catch(() => {})
  place(null)
  const timer = setInterval(refresh, 350)
  win.once('closed', () => {
    ended = true
    clearInterval(timer)
    contents.removeListener('before-mouse-event', mouse)
    if (!toolbar.webContents.isDestroyed()) toolbar.webContents.close()
  })
  return {
    toolbar: toolbar.webContents,
    contents, win, view,
    get cropped() { return Boolean(crop) },
    toggle() { full = !full; lastLabel = ''; if (full) place(null); else void refresh() },
  }
}
module.exports = { mountLayout }
