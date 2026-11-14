import checksums from 'electron/checksums.json' with { type: 'json' }
import { execFileSync } from 'node:child_process'
import path from 'node:path'

// 固定证书指纹，防止同名证书被重新创建后悄然改变应用身份；不包含私钥。
export const LOCAL_SIGNING_CERTIFICATE = {
  name: 'gongluERP Local Signing',
  sha1: 'C2028A6A705F9DE02487824CC44F07E9B7E8D400',
}

export function checkLocalSigningCertificate(context, execute = execFileSync) {
  if (context.electronPlatformName !== 'darwin') return
  const identities = execute('/usr/bin/security', ['find-identity', '-v', '-p', 'codesigning'], { encoding: 'utf8' })
  if (!identities.includes(LOCAL_SIGNING_CERTIFICATE.sha1)) {
    throw new Error(`未找到可用的 ${LOCAL_SIGNING_CERTIFICATE.name} 原证书及私钥，请恢复原签名身份后重新打包。`)
  }
}

export function verifyDesktopSignature(context, mode, execute = execFileSync) {
  if (context.electronPlatformName !== 'darwin') return
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`)
  const anchor = mode === 'local'
    ? `anchor = H"${LOCAL_SIGNING_CERTIFICATE.sha1}"`
    : 'anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.13] exists'
  for (const [target, requirement] of [
    [app, `${anchor} and identifier "${context.packager.appInfo.id}"`],
    [path.join(app, 'Contents/Resources/runtime/champion-erp-runtime'), anchor],
  ]) {
    try {
      execute('/usr/bin/codesign', ['--verify', '--deep', '--strict', '-R', `=${requirement}`, target], { stdio: 'pipe' })
    } catch (cause) {
      throw new Error(`桌面签名校验失败：${target}，请检查应用及本机执行器是否使用预期证书签名。`, { cause })
    }
  }
}

export function createBuilderConfig(environment = process.env) {
  const mode = environment.ERP_DESKTOP_SIGNING_MODE || 'local'
  if (!['local', 'developer-id'].includes(mode)) throw new Error('桌面签名方式必须为 local 或 developer-id。')
  return {
    electronDownload: {
      // 显式设置 force，让 electron-builder 26 按 @electron/get 配置读取 checksums。
      force: false,
      checksums,
    },
    mac: mode === 'local'
      ? { identity: LOCAL_SIGNING_CERTIFICATE.sha1, forceCodeSigning: true, hardenedRuntime: false, notarize: false, timestamp: 'none' }
      : { forceCodeSigning: true, hardenedRuntime: true, notarize: true },
    beforePack: mode === 'local' ? checkLocalSigningCertificate : undefined,
    afterSign: context => verifyDesktopSignature(context, mode),
  }
}

export default createBuilderConfig()
