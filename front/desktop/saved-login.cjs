const fs = require('node:fs')
const path = require('node:path')
const { createHash, randomUUID } = require('node:crypto')

// 每个后端独立保存。系统钥匙串不可用时仍允许普通登录。
class SavedLogin {
  constructor(directory, origin, storage) {
    this.storage = storage
    const site = createHash('sha256').update(origin).digest('hex')
    this.file = path.join(directory, 'login-' + site + '.bin')
  }

  available() {
    return this.storage.isEncryptionAvailable() && (process.platform !== 'linux' ||
      ['gnome_libsecret', 'kwallet', 'kwallet5', 'kwallet6'].includes(this.storage.getSelectedStorageBackend()))
  }

  read() {
    if (!fs.existsSync(this.file) || !this.available()) return {}
    try { return JSON.parse(this.storage.decryptString(fs.readFileSync(this.file))) }
    catch { return {} }
  }

  write(value) {
    if (!value.password && !value.cookie) { fs.rmSync(this.file, { force: true }); return }
    if (!this.available()) throw new Error('系统密码存储不可用。')
    const bytes = this.storage.encryptString(JSON.stringify(value))
    const temp = this.file + '.' + randomUUID()
    try {
      fs.writeFileSync(temp, bytes, { mode: 0o600, flag: 'wx' })
      fs.renameSync(temp, this.file)
    } finally { fs.rmSync(temp, { force: true }) }
  }

  forgetSession(forgetPassword = false) {
    const old = this.read()
    this.write(!forgetPassword && old.password ? { email: old.email, password: old.password } : {})
  }
}

function registerSavedLogin({ ipcMain, win, url, vault, session }) {
  function trusted(event) {
    const frame = event.senderFrame
    if (!frame || event.sender !== win.webContents || frame !== win.webContents.mainFrame ||
        new URL(frame.url).origin !== new URL(url).origin || new URL(frame.url).pathname !== '/login') {
      throw new Error('仅桌面登录页可使用已保存的登录信息。')
    }
  }
  ipcMain.handle('account:saved-login', event => {
    trusted(event)
    const old = session.cookie ? {} : vault.read()
    return { available: vault.available(), email: old.email || '', password: old.password || '' }
  })
  ipcMain.handle('account:remember-login', (event, options) => {
    trusted(event)
    if (!session.cookie || !session.email || options.email.trim().toLowerCase() !== session.email ||
        typeof options.password !== 'string' || options.password.length > 256) throw new Error('登录状态已变更。')
    vault.write({
      email: session.email, password: options.remember === true ? options.password : '',
      ...(options.auto === true ? { cookie: session.cookie, csrf: session.csrf } : {}),
    })
  })
  win.once('closed', () => {
    ipcMain.removeHandler('account:saved-login')
    ipcMain.removeHandler('account:remember-login')
  })
}

module.exports = { SavedLogin, registerSavedLogin }
