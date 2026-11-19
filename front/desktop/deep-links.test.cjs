const assert = require('node:assert/strict')
const { test } = require('node:test')
const { EventEmitter } = require('node:events')
const { DESKTOP_SCHEME, DEV_DESKTOP_SCHEME, parseDesktopLink, desktopLinkArgument, registerDesktopHandoff } = require('./deep-links.cjs')

const request = 'a'.repeat(43)
const origin = 'https://erp.example.test'
const link = `${DESKTOP_SCHEME}://continue?request=${request}&origin=${encodeURIComponent(origin)}`

test('开发和发布客户端只接受各自的链接协议', () => {
  const developmentLink = link.replace(DESKTOP_SCHEME, DEV_DESKTOP_SCHEME)
  assert.equal(parseDesktopLink(developmentLink, origin, DEV_DESKTOP_SCHEME), request)
  assert.throws(() => parseDesktopLink(link, origin, DEV_DESKTOP_SCHEME))
  assert.throws(() => parseDesktopLink(developmentLink, origin))
  assert.equal(desktopLinkArgument([link, developmentLink], DEV_DESKTOP_SCHEME), developmentLink)
  assert.equal(desktopLinkArgument([developmentLink]), undefined)
})

test('冷启动和 Windows 重复启动参数使用同一个链接解析，站点必须匹配配置', () => {
  assert.equal(desktopLinkArgument(['app.exe', '--flag', link, '--other']), link)
  assert.equal(parseDesktopLink(link, origin), request)
  assert.equal(parseDesktopLink(link, origin + '/'), request)
  for (const invalid of [
    link.replace('continue', 'execute'),
    link + '&request=' + request,
    link + '&command=run',
    link + '#fragment',
    link.replace(request, '../invalid'),
    link.replace('continue', 'user@continue'),
    link.replace(encodeURIComponent(origin), encodeURIComponent('https://other.example.test')),
  ]) assert.throws(() => parseDesktopLink(invalid, origin))
})

test('主页面可在登录后领取早到的 macOS 链接，其他页面不能读取或清除请求', async () => {
  const handlers = new Map()
  const ipcMain = { handle: (name, handler) => handlers.set(name, handler), removeHandler: name => handlers.delete(name) }
  const frame = { url: 'http://127.0.0.1:1234/login' }
  const win = new EventEmitter()
  win.webContents = { mainFrame: frame }
  let pending = request
  registerDesktopHandoff({ ipcMain, win, frontendUrl: 'http://127.0.0.1:1234/', pending: () => pending,
    finish: id => { if (id === pending) pending = '' } })
  const event = { sender: win.webContents, senderFrame: frame }
  assert.equal(handlers.get('desktop:pending-handoff')(event), request)
  assert.throws(() => handlers.get('desktop:pending-handoff')({ ...event, senderFrame: { url: frame.url } }))
  assert.throws(() => handlers.get('desktop:finish-handoff')({ ...event, sender: {} }, request))
  handlers.get('desktop:finish-handoff')(event, 'other')
  assert.equal(pending, request)
  handlers.get('desktop:finish-handoff')(event, request)
  assert.equal(pending, '')
  win.emit('closed')
  assert.equal(handlers.size, 0)
})
