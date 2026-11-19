const { test } = require('node:test')
const assert = require('node:assert/strict')
const { openAlibabaAuthorization } = require('./alibaba-authorization.cjs')

const url = 'https://auth.1688.com/auth/authorize.htm?state=private'
function browser(platform, bundle) {
  const calls = []
  return {
    calls, platform,
    app: { getApplicationInfoForProtocol: async () => ({ path: bundle }) },
    shell: { openExternal: async value => calls.push(value) },
    execute: async (...args) => calls.push(args),
  }
}

test('Mac Chrome 单独启动可见新窗口，并把完整授权地址作为一个参数传递', async () => {
  const bundle = '/Applications/Google Chrome.app'
  const options = browser('darwin', bundle)
  await openAlibabaAuthorization(url, options)
  assert.deepEqual(options.calls, [['/usr/bin/open', ['-n', '-a', bundle, '--args', '--new-window', url]]])
})

test('其他默认浏览器和系统使用系统链接处理程序', async () => {
  for (const options of [browser('darwin', '/Applications/Safari.app'), browser('win32')]) {
    await openAlibabaAuthorization(url, options)
    assert.deepEqual(options.calls, [url])
  }
})

test('打开万邦授权链接，只允许该入口与单个 state', async () => {
  const options = browser('win32')
  const link = 'https://o0b.cn/9vXze?state=vendor-key'
  await openAlibabaAuthorization(link, options)
  for (const value of ['https://o0b.cn/other?state=vendor-key', 'https://o0b.cn/9vXze',
    link + '&state=other', link + '&redirect_uri=https://example.com', link + '#fragment']) {
    await assert.rejects(openAlibabaAuthorization(value, options), /授权地址无效/)
  }
  assert.deepEqual(options.calls, [link])
})

test('拒绝未知授权地址，启动出错时不泄露一次性参数', async () => {
  const options = browser('darwin', '/Applications/Google Chrome.app')
  for (const value of ['https://example.com/auth/authorize.htm', 'https://auth.1688.com/other', 'file:///Applications/test']) {
    await assert.rejects(openAlibabaAuthorization(value, options), /授权地址无效/)
  }
  assert.deepEqual(options.calls, [])
  options.execute = async () => { throw new Error(url) }
  await assert.rejects(openAlibabaAuthorization(url, options), { message: '无法打开系统浏览器，请重新授权。' })
})
