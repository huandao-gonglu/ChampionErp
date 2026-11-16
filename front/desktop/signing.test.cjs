const assert = require('node:assert/strict')
const { test } = require('node:test')
const path = require('node:path')
const fs = require('node:fs/promises')
const os = require('node:os')
const { execFileSync } = require('node:child_process')

const signing = import('../electron-builder.config.mjs')
const context = {
  electronPlatformName: 'darwin', appOutDir: '/temporary/build',
  packager: { appInfo: { productFilename: 'gongluErp', id: 'com.champion.erp.front' } },
}

function signedDescription(args) {
  const identifier = args.at(-1).endsWith('champion-erp-runtime') ? 'champion-erp-runtime' : 'com.champion.erp.front'
  return `designated => identifier "${identifier}" and certificate leaf = H"c2028a6a705f9de02487824cc44f07e9b7e8d400"\n`
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
  verifyDesktopSignature(context, 'local', (command, args) => {
    if (args[0] === '--display') return signedDescription(args)
    calls.push([command, args])
  })
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
    if (args[0] === '--display') return signedDescription(args)
  }), /桌面签名校验失败/)
})

test('证书正确但授权身份绑定单个版本、弱化身份或缺少规则时拒绝打包', async () => {
  const { verifyDesktopSignature } = await signing
  for (const description of [
    'designated => cdhash H"bf5ec14063cde638b6d90457591527d36870b42a"\n',
    signedDescription(['app']).trim() + ' and info[CFBundleShortVersionString] = "1.2.84"\n',
    'designated => identifier "com.champion.erp.front"\n',
    '',
  ]) {
    assert.throws(() => verifyDesktopSignature(context, 'local', (_command, args) => {
      if (args[0] === '--display') return description
    }), /授权身份规则必须只绑定应用标识和固定证书/)
  }
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

test('原生签名的不同构建互相满足身份规则，附加版本条件的签名被发布检查拒绝', async t => {
  if (process.platform !== 'darwin') return t.skip('原生签名回归仅在 Mac 执行。')
  const { LOCAL_SIGNING_CERTIFICATE, createBuilderConfig } = await signing
  const identities = execFileSync('/usr/bin/security', ['find-identity', '-v', '-p', 'codesigning'], { encoding: 'utf8' })
  if (!identities.includes(LOCAL_SIGNING_CERTIFICATE.sha1)) return t.skip('本机未安装项目签名证书及私钥。')
  try { execFileSync('/usr/bin/xcode-select', ['-p'], { stdio: 'pipe' }) }
  catch { return t.skip('本机未安装编译工具。') }
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'erp-signing-regression-'))
  t.after(() => fs.rm(directory, { recursive: true, force: true }))
  const execute = (command, args) => execFileSync(command, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
  const sign = (target, extra = []) => execute('/usr/bin/codesign', [
    '--force', '--sign', LOCAL_SIGNING_CERTIFICATE.sha1, '--timestamp=none', ...extra, target,
  ])
  const requirements = [], executableContents = [], builds = []
  const config = createBuilderConfig({})
  for (const version of [1, 2]) {
    const build = { ...context, appOutDir: path.join(directory, `v${version}`) }
    const app = path.join(build.appOutDir, 'gongluErp.app')
    const executable = path.join(app, 'Contents/MacOS/gongluErp')
    const runtime = path.join(app, 'Contents/Resources/runtime/champion-erp-runtime')
    await fs.mkdir(path.dirname(executable), { recursive: true })
    await fs.mkdir(path.dirname(runtime), { recursive: true })
    await fs.writeFile(path.join(app, 'Contents/Info.plist'), `<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.champion.erp.front</string>
<key>CFBundleExecutable</key><string>gongluErp</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>1.0.${version}</string>
</dict></plist>`)
    const source = path.join(directory, `v${version}.c`)
    await fs.writeFile(source, `int main(void) { return ${version}; }\n`)
    execute('/usr/bin/clang', [source, '-o', executable])
    await fs.copyFile(executable, runtime)
    sign(runtime, ['--identifier', 'champion-erp-runtime'])
    sign(app)
    config.afterSign(build)
    requirements.push(execute('/usr/bin/codesign', ['--display', '-r-', app]).match(/^designated => (.+)$/m)[1])
    executableContents.push(await fs.readFile(executable))
    builds.push({ build, app })
  }
  assert.notDeepEqual(executableContents[0], executableContents[1])
  assert.equal(requirements[0], requirements[1])
  execute('/usr/bin/codesign', ['--verify', '--strict', '-R', `=${requirements[0]}`, builds[1].app])
  execute('/usr/bin/codesign', ['--verify', '--strict', '-R', `=${requirements[1]}`, builds[0].app])

  sign(builds[1].app, ['--requirements', `=designated => ${requirements[1]} and info[CFBundleShortVersionString] = "1.0.2"`])
  execute('/usr/bin/codesign', ['--verify', '--deep', '--strict', builds[1].app])
  assert.throws(() => config.afterSign(builds[1].build), /授权身份规则必须只绑定应用标识和固定证书/)
})
