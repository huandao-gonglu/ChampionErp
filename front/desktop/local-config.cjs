const { randomUUID } = require('node:crypto')
const LOCAL_FIELDS = new Set(['command', 'profile', 'sandbox', 'browser_mode', 'browser_profile', 'browser_port', 'browser_url', 'browser_proxy'])

function registerLocalConfig({ ipcMain, win, frontendUrl, runtime }) {
  const origin = new URL(frontendUrl).origin
  ipcMain.handle('desktop:configure-models', async (event, models) => {
    const frame = event.senderFrame
    if (event.sender !== win.webContents || !frame || frame !== win.webContents.mainFrame || new URL(frame.url).origin !== origin) {
      throw new Error('仅应用主页面可以修改本机连接设置。')
    }
    if (!models || typeof models !== 'object' || Array.isArray(models) || Object.keys(models).length > 100) throw new Error('本机模型配置无效。')
    for (const [id, model] of Object.entries(models)) {
      if (!id || id.length > 200 || !model || typeof model !== 'object' || Array.isArray(model)) throw new Error('本机模型标识无效。')
      if (Object.entries(model).some(([key, value]) => !LOCAL_FIELDS.has(key) || typeof value !== 'string' || value.length > 4096)) throw new Error('本机模型配置包含未知字段或无效值。')
    }
    const reply = await runtime.perform({ id: randomUUID(), operation: 'config.write', params: { models }, timeout: 30 })
    if (!reply.ok) throw new Error(reply.error || '本机连接设置未保存。')
    return reply.result
  })
  win.once('closed', () => ipcMain.removeHandler('desktop:configure-models'))
}
module.exports = { registerLocalConfig }
