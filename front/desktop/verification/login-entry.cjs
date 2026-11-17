// 在原商品文档中执行。优先打开商品价格、优惠和规格旁的登录入口，其次使用站点的登录入口。
function openLoginEntry(product, panel) {
  if (location.origin !== 'https://detail.1688.com' || location.origin + location.pathname !== product) return 'waiting'
  if (panel) return panel.reason === 'LOGIN' ? 'opened' : 'waiting'
  const visible = el => el.checkVisibility({ opacityProperty: true, visibilityProperty: true })
  if ([...document.querySelectorAll('dialog[open],[aria-modal=true],input[type=password],input[autocomplete=one-time-code]')].some(visible)) return 'waiting'

  const offerEntry = /^登录(?:后)?(?:查看|看)(?:价格|报价|优惠价|批发价|拿货价|专享价|全部规格|全部|更多优惠)?$/
  const signin = /^(?:请|立即|去)?登录$/
  const controls = 'a,button,[role=button],od-button'
  let fallback, outside
  for (const el of document.querySelectorAll(controls + ',od-text,span,div')) {
    // 含有多个控件的父容器、登录表单及被遮挡的入口都不能作为点击目标。
    if (el.children.length && !el.matches(controls)) continue
    const label = (el.getAttribute('aria-label') || el.textContent || '').trim()
    const unlocksOffer = offerEntry.test(label)
    if (!unlocksOffer && !signin.test(label)) continue
    const control = el.closest(controls) || el
    if (control.closest('form,[role=dialog],[aria-modal=true],[inert],[aria-disabled=true],:disabled') || !visible(control)) continue
    const rect = el.getBoundingClientRect()
    if (!rect.width || !rect.height) continue
    if (rect.right <= 0 || rect.bottom <= 0 || rect.left >= innerWidth || rect.top >= innerHeight) {
      // 1688 桌面导航可能宽于内置窗口，例如“登录”入口位于右边缘之外。
      if (control.matches(controls) || getComputedStyle(el).cursor === 'pointer') outside ||= el
      continue
    }
    const x = (Math.max(rect.left, 0) + Math.min(rect.right, innerWidth)) / 2
    const y = (Math.max(rect.top, 0) + Math.min(rect.bottom, innerHeight)) / 2
    if (!control.contains(document.elementFromPoint(x, y))) continue
    if (unlocksOffer) {
      el.click()
      return 'opened'
    }
    fallback ||= el
  }
  const entry = fallback || outside
  if (!entry) return 'waiting'
  if (!fallback) entry.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  entry.click()
  return 'opened'
}
module.exports = { openLoginEntry }
