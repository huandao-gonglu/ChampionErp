import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'
import { formatNotices, type NoticeCatalog } from './notice-catalog'

type SourceCatalog = NoticeCatalog & { inputFiles: string[]; inputDigest: string }

export function selectNotices(catalog: NoticeCatalog, desktop: boolean, modules?: string[]): NoticeCatalog {
  const packages = catalog.packages.filter(item => {
    if (item.ecosystem !== 'npm' || desktop || !modules) return true
    return item.paths?.some(path => modules.some(id => id.startsWith(`${path}/`)))
  }).map(item => {
    const { paths, ...published } = item
    void paths
    return published
  })
  const used = new Set(packages.flatMap(item => item.notices.flatMap(notice => notice.parts.map(part => part.text))))
  return { packages, texts: Object.fromEntries(Object.entries(catalog.texts).filter(([id]) => used.has(id))) }
}

export function licenseFiles(catalog: NoticeCatalog, electron?: { license: string; chromium: string }): Record<string, string> {
  const files: Record<string, string> = {
    'licenses/THIRD-PARTY-NOTICES.txt': '第三方组件许可证与声明\n\n' + formatNotices(catalog) + '\n',
  }
  if (electron) {
    files['licenses/Electron/LICENSE.txt'] = electron.license
    files['licenses/Electron/LICENSES.chromium.html'] = electron.chromium
  }
  return files
}

export function thirdPartyNotices(desktop: boolean): Plugin {
  const root = fileURLToPath(new URL('..', import.meta.url)).replace(/[\\/]$/, '')
  const catalog = JSON.parse(readFileSync(resolve(root, 'src/legal/third-party-notices.json'), 'utf8')) as SourceCatalog
  const digest = createHash('sha256')
  for (const file of catalog.inputFiles) {
    digest.update(file)
    digest.update(readFileSync(resolve(root, '..', file)))
  }
  if (digest.digest('hex') !== catalog.inputDigest) {
    throw new Error('第三方声明已过期，请在仓库根目录运行 .venv/bin/python scripts/generate_third_party_notices.py')
  }
  // 原样随包提供 Electron 的官方声明，不再解析为页面数据或重复展开 Chromium credits。
  const electron = desktop ? {
    license: readFileSync(resolve(root, 'node_modules/electron/dist/LICENSE'), 'utf8'),
    chromium: readFileSync(resolve(root, 'node_modules/electron/dist/LICENSES.chromium.html'), 'utf8'),
  } : undefined
  return {
    name: 'third-party-notices',
    configureServer(server) {
      if (desktop) {
        for (const [file, text] of Object.entries(licenseFiles(selectNotices(catalog, true), electron))) {
          const destination = resolve(root, 'dist-desktop', file)
          mkdirSync(resolve(destination, '..'), { recursive: true })
          writeFileSync(destination, text)
        }
      }
      server.middlewares.use('/licenses/THIRD-PARTY-NOTICES.txt', (_request, response) => {
        response.setHeader('Content-Type', 'text/plain; charset=utf-8')
        response.setHeader('Content-Disposition', 'attachment; filename="THIRD-PARTY-NOTICES.txt"')
        response.end(licenseFiles(selectNotices(catalog, desktop))['licenses/THIRD-PARTY-NOTICES.txt'])
      })
    },
    generateBundle(_options, bundle) {
      // Web 仅收录实际进入产物的模块；桌面还会交付生产 node_modules，保留其完整闭包。
      const modules = Object.values(bundle).flatMap(output => output.type === 'chunk'
        ? Object.entries(output.modules).filter(([, info]) => info.renderedLength > 0).map(([id]) => id.replaceAll('\\', '/'))
        : [])
      const prefix = `${root.replaceAll('\\', '/')}/`
      const relative = modules.map(id => id.startsWith(prefix) ? id.slice(prefix.length) : id)
      const knownPaths = catalog.packages.flatMap(item => item.paths ?? [])
      for (const id of relative.filter(id => id.startsWith('node_modules/'))) {
        if (!knownPaths.some(path => id.startsWith(`${path}/`))) {
          this.error(`产物包含未登记的第三方依赖：${id}，请更新运行依赖与第三方声明。`)
        }
      }
      for (const [fileName, source] of Object.entries(licenseFiles(selectNotices(catalog, desktop, relative), electron))) {
        this.emitFile({ type: 'asset', fileName, source })
      }
    },
  }
}
