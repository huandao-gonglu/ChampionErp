const { execFile } = require('node:child_process')
const { promisify } = require('node:util')

// 证书指纹是公开身份，不包含私钥；与打包和签名检查共用。
const LOCAL_SIGNING_CERTIFICATE = {
  name: 'gongluERP Local Signing',
  sha1: 'C2028A6A705F9DE02487824CC44F07E9B7E8D400',
}
const executeFile = promisify(execFile)

async function canUseNativeUpdates(bundle, { platform = process.platform, identifier = 'com.champion.erp.front', execute = executeFile } = {}) {
  if (platform !== 'darwin') return false
  const requirement = `identifier "${identifier}" and (certificate leaf = H"${LOCAL_SIGNING_CERTIFICATE.sha1}" or (anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.13] exists))`
  try {
    await execute('/usr/bin/codesign', ['--verify', '--deep', '--strict', '-R', `=${requirement}`, bundle])
    return true
  } catch { return false }
}

module.exports = { LOCAL_SIGNING_CERTIFICATE, canUseNativeUpdates }
