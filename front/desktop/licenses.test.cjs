const { test } = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { registerLicenseDirectory } = require('./licenses.cjs')

function setup(error = '') {
  let handler
  let removed
  const opened = []
  const win = new EventEmitter()
  win.webContents = { mainFrame: { url: 'http://127.0.0.1:54321/settings' } }
  registerLicenseDirectory({
    ipcMain: { handle(channel, callback) { assert.equal(channel, 'licenses:open'); handler = callback }, removeHandler(channel) { removed = channel } },
    shell: { async openPath(directory) { opened.push(directory); return error } },
    win, frontendUrl: 'http://127.0.0.1:54321/', directory: '/application/resources/licenses',
  })
  return { win, handler, opened, removed: () => removed }
}

test('只打开固定许可证目录，关闭窗口时注销入口', async () => {
  const { win, handler, opened, removed } = setup()
  await handler({ sender: win.webContents, senderFrame: win.webContents.mainFrame }, '/arbitrary/path')
  assert.deepEqual(opened, ['/application/resources/licenses'])
  win.emit('closed')
  assert.equal(removed(), 'licenses:open')
})

test('拒绝其他窗口、子框架及导航到外部页面后的调用', async () => {
  const { win, handler, opened } = setup()
  await assert.rejects(handler({ sender: {}, senderFrame: win.webContents.mainFrame }))
  await assert.rejects(handler({ sender: win.webContents, senderFrame: { url: 'http://127.0.0.1:54321/' } }))
  win.webContents.mainFrame.url = 'https://example.com'
  await assert.rejects(handler({ sender: win.webContents, senderFrame: win.webContents.mainFrame }))
  assert.deepEqual(opened, [])
})

test('系统打开失败传回失败状态，不误报成功', async () => {
  const { win, handler } = setup('无法找到文件')
  await assert.rejects(handler({ sender: win.webContents, senderFrame: win.webContents.mainFrame }), /无法打开许可证目录/)
})

test('预加载仅声明本机能力并暴露无参数的许可证入口', async () => {
  let bridge
  const calls = []
  const ipcRenderer = new (require('node:events').EventEmitter)()
  ipcRenderer.invoke = (...args) => { calls.push(args); return Promise.resolve() }
  vm.runInNewContext(readFileSync(path.join(__dirname, 'preload.cjs'), 'utf8'), {
    require(name) {
      assert.equal(name, 'electron')
      return {
        contextBridge: { exposeInMainWorld(name, api) { assert.equal(name, 'championDesktop'); bridge = api } },
        ipcRenderer,
      }
    },
  })
  assert.deepEqual(Object.keys(bridge), ['localOperations', 'savedLogin', 'rememberLogin', 'onConnectionChanged', 'openNewAPI', 'configureModels', 'openLicenses'])
  const events = []
  const unsubscribe = bridge.onConnectionChanged((...args) => events.push(args))
  ipcRenderer.emit('desktop:connection-changed', { sender: '主进程' })
  unsubscribe()
  ipcRenderer.emit('desktop:connection-changed', {})
  assert.deepEqual(events, [[]])
  assert.equal(bridge.localOperations, true)
  await bridge.openLicenses('/arbitrary/path')
  assert.deepEqual(calls, [['licenses:open']])
})
