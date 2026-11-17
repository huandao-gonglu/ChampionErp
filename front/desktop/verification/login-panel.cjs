// 此函数在验证窗口内执行。只返回布局信息，表单值和二维码内容留在原网页。
function inspectPanel() {
  const modalSelector = 'dialog,[role="dialog"],[aria-modal="true"],[class*="modal" i],[class*="dialog" i],[id*="modal" i],[id*="dialog" i]'
  const fieldSelector = 'input[type="password"],input[type="tel"],input[autocomplete="tel"],input[autocomplete="one-time-code"],input[placeholder*="手机号"],input[placeholder*="验证码"],input[placeholder*="密码"]'
  const official = host => /(^|\.)(1688|taobao|alibaba|tmall|alicdn|alipay)\.com$/.test(host)
  const visible = el => el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) && el.getClientRects().length > 0
  function geometry(el) {
    const { x, y, width, height } = el.getBoundingClientRect()
    return { x, y, width, height }
  }
  function bounded(el) {
    const r = el.getBoundingClientRect()
    return visible(el) && r.width >= 80 && r.height >= 40 && r.left >= -0.5 && r.top >= -0.5 &&
      r.right <= innerWidth + 0.5 && r.bottom <= innerHeight + 0.5 &&
      (r.width < innerWidth - 12 || r.height < innerHeight - 12)
  }
  function panel(el, reason, host) {
    return { reason, host, ...geometry(el) }
  }
  function enclosingPanel(anchor) {
    const ancestors = []
    for (let el = anchor; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
      ancestors.push(el)
    }
    let insideLayer = false
    for (const el of ancestors.reverse()) {
      const style = getComputedStyle(el)
      const positioned = ['fixed', 'absolute'].includes(style.position)
      const modal = el.matches(modalSelector)
      insideLayer ||= positioned || modal
      if (!bounded(el)) continue
      const painted = style.backgroundColor !== 'rgba(0, 0, 0, 0)' || style.boxShadow !== 'none'
      if (modal || positioned || (insideLayer && painted)) return el
    }
    return null
  }
  const children = []
  let embedded = null, fullFrame = null
  for (const frame of document.querySelectorAll('iframe[src]')) {
    if (!visible(frame)) continue
    let url
    try { url = new URL(frame.src) } catch { continue }
    if (!official(url.hostname) || url.protocol !== 'https:') continue
    const r = frame.getBoundingClientRect()
    if (r.width < 80 || r.height < 40 || r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth) continue
    const login = /^(login|passport)\./i.test(url.hostname) || /\/(login|signin)([./]|$)/i.test(url.pathname)
    const captcha = /^(slide|captcha)\./.test(url.hostname) || /captcha|_____tmd_____|\/(punish|verify)([/.]|$)/i.test(url.pathname)
    if (login || captcha) {
      const outer = enclosingPanel(frame.parentElement) || (bounded(frame) ? frame : null)
      if (!outer && !fullFrame) fullFrame = panel(frame, login ? 'LOGIN' : 'CAPTCHA', url.hostname)
      if (outer && !embedded) embedded = panel(outer, login ? 'LOGIN' : 'CAPTCHA', url.hostname)
    }
    // 同源路径可能在登录跳转时变化；父子位置只在目标域名唯一对应时使用。
    const scaleX = r.width / frame.offsetWidth, scaleY = r.height / frame.offsetHeight
    children.push({ host: url.hostname, x: r.x + frame.clientLeft * scaleX, y: r.y + frame.clientTop * scaleY,
      width: frame.clientWidth * scaleX, height: frame.clientHeight * scaleY })
  }
  if (embedded) return { box: embedded, children: [] }
  for (const field of document.querySelectorAll(fieldSelector)) {
    if (!visible(field)) continue
    const el = enclosingPanel(field.parentElement)
    if (el && /登录|登錄|sign in/i.test(el.innerText)) return { box: panel(el, 'LOGIN', location.hostname), children: [] }
  }
  // 二维码模式可能不渲染输入框。只在有明确登录操作的弹窗内辨认。
  const loginContainers = modalSelector + ',[class*="login" i],[id*="login" i]'
  for (const el of document.querySelectorAll(loginContainers)) {
    if (!bounded(el) || !/(扫码登录|密码登录|短信登录)/.test(el.innerText)) continue
    const outer = enclosingPanel(el)
    if (outer) return { box: panel(outer, 'LOGIN', location.hostname), children: [] }
  }
  return { box: fullFrame, children }
}

const panelExpression = '(' + inspectPanel.toString() + ')()'

async function readPanel(contents) {
  const { box, children } = await contents.executeJavaScript(panelExpression)
  if (children.length) {
    const frames = contents.mainFrame.frames
    for (const child of children) {
      if (children.filter(item => item.host === child.host).length !== 1) continue
      const matching = frames.filter(frame => {
        try { return new URL(frame.url).origin === 'https://' + child.host } catch { return false }
      })
      if (matching.length !== 1) continue
      // 跨域登录页由自己的渲染进程测量，再映射回商品页的坐标。
      const inner = await matching[0].executeJavaScript(`(() => {
        const {box} = ${panelExpression}
        return box && {box, w: innerWidth, h: innerHeight}
      })()`).catch(() => null)
      if (!inner || !inner.w || !inner.h) continue
      const sx = child.width / inner.w, sy = child.height / inner.h
      return { ...inner.box, x: child.x + inner.box.x * sx, y: child.y + inner.box.y * sy,
        width: inner.box.width * sx, height: inner.box.height * sy }
    }
  }
  return box
}
module.exports = { panelExpression, readPanel }
