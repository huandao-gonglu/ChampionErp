const assert = require('node:assert/strict')
const { test } = require('node:test')
const path = require('node:path')

const signing = import('../electron-builder.config.mjs')
const context = {
  electronPlatformName: 'darwin', appOutDir: '/temporary/build',
  packager: { appInfo: { productFilename: 'gongluErp', id: 'com.champion.erp.front' } },
}

test('普通打包固定原证书，正式签名使用独立配置；未知方式拒绝构建', async () => {
  const { createBuilderConfig, LOCAL_SIGNING_CERTIFICATE } = await signing
  const local = createBuilderConfig({})
  assert.equal(local.mac.identity, LOCAL_SIGNING_CERTIFICATE.sha1)
  assert.equal(local.mac.forceCodeSigning, true)
  assert.equal(local.mac.hardenedRuntime, false)
  assert.equal(local.mac.notarize, false)
  assert.equal(local.mac.timestamp, 'none')
  const official = createBuilderConfig({ ERP_DESKTOP_SIGNING_MODE: 'developer-id' })
  assert.equal(official.mac.identity, undefined)
  assert.equal(official.mac.forceCodeSigning, true)
  assert.equal(official.mac.hardenedRuntime, true)
  assert.equal(official.mac.notarize, true)
  assert.equal(official.beforePack, undefined)
  assert.throws(() => createBuilderConfig({ ERP_DESKTOP_SIGNING_MODE: 'ad-hoc' }), /签名方式/)
})

test('原证书可用才允许打包，同名新证书不能替换原身份', async () => {
  const { checkLocalSigningCertificate, LOCAL_SIGNING_CERTIFICATE } = await signing
  checkLocalSigningCertificate(context, () => `1) ${LOCAL_SIGNING_CERTIFICATE.sha1} "${LOCAL_SIGNING_CERTIFICATE.name}"`)
  for (const identities of ['0 valid identities found', `1) ${'A'.repeat(40)} "${LOCAL_SIGNING_CERTIFICATE.name}"`]) {
    assert.throws(() => checkLocalSigningCertificate(context, () => identities), /原证书及私钥/)
  }
})

test('签名后复核主应用标识和执行器的证书，拒绝临时签名或执行器漏签', async () => {
  const { verifyDesktopSignature, LOCAL_SIGNING_CERTIFICATE } = await signing
  const calls = []
  verifyDesktopSignature(context, 'local', (command, args) => calls.push([command, args]))
  assert.equal(calls.length, 2)
  const app = path.join(context.appOutDir, 'gongluErp.app')
  assert.equal(calls[0][1].at(-1), app)
  assert.match(calls[0][1][4], /identifier "com.champion.erp.front"/)
  assert.equal(calls[1][1].at(-1), path.join(app, 'Contents/Resources/runtime/champion-erp-runtime'))
  for (const [command, args] of calls) {
    assert.equal(command, '/usr/bin/codesign')
    assert.deepEqual(args.slice(0, 4), ['--verify', '--deep', '--strict', '-R'])
    assert.ok(args[4].startsWith('='))
    assert.ok(args[4].includes(LOCAL_SIGNING_CERTIFICATE.sha1))
  }
  assert.throws(() => verifyDesktopSignature(context, 'local', () => { throw new Error('证书不符') }), /桌面签名校验失败/)
  assert.throws(() => verifyDesktopSignature(context, 'local', (_command, args) => {
    if (args.at(-1).endsWith('champion-erp-runtime')) throw new Error('执行器未签名')
  }), /桌面签名校验失败/)
})

test('正式发布复核 Developer ID，其他平台不读取 Mac 钥匙串或执行 codesign', async () => {
  const { checkLocalSigningCertificate, verifyDesktopSignature } = await signing
  verifyDesktopSignature(context, 'developer-id', (_command, args) => {
    assert.match(args[4], /anchor apple generic/)
    assert.match(args[4], /1\.2\.840\.113635\.100\.6\.1\.13/)
  })
  for (const electronPlatformName of ['win32', 'linux']) {
    const execute = () => { throw new Error('不应访问 Mac 签名工具') }
    checkLocalSigningCertificate({ ...context, electronPlatformName }, execute)
    verifyDesktopSignature({ ...context, electronPlatformName }, 'local', execute)
  }
})
