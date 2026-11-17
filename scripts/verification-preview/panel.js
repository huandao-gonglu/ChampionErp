const toolbar = location.hash === '#toolbar'
document.querySelector('main').hidden = toolbar
const bar = document.querySelector('aside')
bar.hidden = !toolbar
if (toolbar) bar.style.display = 'flex'
const output = document.querySelector('output')
window.preview.onStatus(state => {
  if (toolbar) {
    bar.querySelector('strong').textContent = state.label
    document.querySelector('#toggle').textContent = state.full ? '自动裁剪' : '显示完整页面'
  } else output.textContent = state.label
})
async function start(url) {
  try { await window.preview.start(url) }
  catch { output.textContent = '请输入有效的 1688 商品详情链接后重试。' }
}
document.querySelector('#demo').onclick = () => start('')
document.querySelector('form').onsubmit = event => {
  event.preventDefault()
  start(document.querySelector('#url').value.trim())
}
document.querySelector('#toggle').onclick = () => window.preview.toggle()
