import { describe, expect, it } from 'vitest'
import { licenseFiles, selectNotices } from './third-party-notices'
import { formatNotices, type NoticeCatalog, type NoticePackage } from './notice-catalog'

const component = (name: string, ecosystem: string, text: string): NoticePackage => ({
  name, ecosystem, version: '1.0', license: 'MIT', homepage: '', noticeStatus: 'included',
  paths: [`node_modules/${name}`],
  notices: [{ source: 'LICENSE', parts: [{ kind: 'notice', text }] }],
})

describe('交付范围内的第三方声明', () => {
  const catalog: NoticeCatalog = {
    packages: [component('vue', 'npm', 'vue'), component('typescript', 'npm', 'ts'),
      component('requests', 'Python', 'python')],
    texts: { vue: 'Vue', ts: 'TypeScript', python: 'Requests', unused: 'unused' },
  }
  it('Web 只保留实际构建模块和后端运行依赖，连同无引用正文一起清理', () => {
    const output = selectNotices(catalog, false, ['node_modules/vue/dist/vue.js'])
    expect(output.packages.map(item => item.name)).toEqual(['vue', 'requests'])
    expect(output.texts).toEqual({ vue: 'Vue', python: 'Requests' })
    expect(output.packages.every(item => !('paths' in item))).toBe(true)
    expect(selectNotices(catalog, false, ['node_modules/vue-other/index.js']).packages.map(item => item.name)).toEqual(['requests'])
  })
  it('桌面保留生产依赖，开发预览提供 Web 生产依赖候选清单', () => {
    expect(selectNotices(catalog, true, []).packages).toHaveLength(3)
    expect(selectNotices(catalog, false).packages.map(item => item.name)).toEqual(['vue', 'typescript', 'requests'])
  })
  it('生成可直接阅读的文本，Electron 上游文件原样交付，不再生成页面 JSON', () => {
    const web = licenseFiles(selectNotices(catalog, false, ['node_modules/vue/index.js']))
    expect(Object.keys(web)).toEqual(['licenses/THIRD-PARTY-NOTICES.txt'])
    expect(web['licenses/THIRD-PARTY-NOTICES.txt']).toContain('vue 1.0')
    expect(web['licenses/THIRD-PARTY-NOTICES.txt']).not.toContain('TypeScript')
    const electron = { license: 'Copyright Electron\n\nMIT original\n', chromium: '<html>Original Chromium notices</html>\n' }
    const desktop = licenseFiles(selectNotices(catalog, true), electron)
    expect(desktop['licenses/Electron/LICENSE.txt']).toBe(electron.license)
    expect(desktop['licenses/Electron/LICENSES.chromium.html']).toBe(electron.chromium)
    expect(Object.keys(desktop).every(path => path.startsWith('licenses/'))).toBe(true)
  })
  it('共享正文保持组件对应关系，各自版权和特殊声明不丢失，同名但不同措辞不合并', () => {
    const packages = ['a', 'b'].map(name => ({ ...component(name, 'npm', name), notices: [
      { source: 'LICENSE', parts: [{ kind: 'notice' as const, text: name }, { kind: 'license' as const, text: 'shared' }] },
      { source: 'NOTICE', parts: [{ kind: 'notice' as const, text: `${name}-notice` }] },
    ] }))
    packages.push({ ...component('c', 'npm', 'other'), notices: [
      { source: 'LICENSE', parts: [{ kind: 'license', text: 'other' }] },
    ] })
    const text = formatNotices({ packages, texts: {
      a: 'Copyright A', b: 'Copyright B', 'a-notice': 'Attribution A', 'b-notice': 'Attribution B',
      shared: 'MIT original terms', other: 'MIT modified terms',
    } })
    expect(text.split('MIT original terms')).toHaveLength(2)
    expect(text.split('License text: [L1]')).toHaveLength(3)
    for (const fragment of ['Copyright A', 'Copyright B', 'Attribution A', 'Attribution B', 'MIT modified terms']) {
      expect(text).toContain(fragment)
    }
  })
  it('未识别的长篇相同声明只展示一次，简短共同版权仍逐项展示', () => {
    const packages = ['a', 'b'].map(name => ({ ...component(name, 'npm', 'copyright'), notices: [
      { source: 'LICENSE', parts: [{ kind: 'notice' as const, text: 'copyright' }, { kind: 'notice' as const, text: 'composite' }] },
    ] }))
    const composite = 'Unmodified upstream license and attribution. '.repeat(20)
    const text = formatNotices({ packages, texts: { copyright: 'Copyright shared authors', composite } })
    expect(text.split(composite)).toHaveLength(2)
    expect(text.split('Notice text: [N1]')).toHaveLength(3)
    expect(text.split('Copyright shared authors')).toHaveLength(3)
  })
})
