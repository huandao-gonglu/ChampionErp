export type NoticePart = { kind: 'license' | 'notice'; text: string }
export type NoticePackage = {
  name: string
  version: string
  ecosystem: string
  license: string
  homepage: string
  noticeStatus: 'included' | 'declaration-only'
  notices: Array<{ source: string; parts: NoticePart[] }>
  paths?: string[]
}
export type NoticeCatalog = { packages: NoticePackage[]; texts: Record<string, string> }

export function formatNotices({ packages, texts }: NoticeCatalog): string {
  const entries = [...packages].sort((left, right) =>
    left.name.localeCompare(right.name, 'en', { sensitivity: 'base' }) ||
    left.version.localeCompare(right.version, 'en') ||
    left.ecosystem.localeCompare(right.ecosystem, 'en')
  )
  const uses = new Map<string, number>()
  for (const entry of entries) {
    for (const notice of entry.notices) {
      for (const part of notice.parts) {
        // 短版权声明逐项展示；未识别的长篇许可或复合 NOTICE 也可按完全相同的原文共享。
        if (part.kind === 'license' || texts[part.text].length > 350) {
          uses.set(part.text, (uses.get(part.text) ?? 0) + 1)
        }
      }
    }
  }
  const shared = new Map<string, string>()
  let licenseNumber = 0
  let noticeNumber = 0
  const sections = entries.map(item => {
    const heading = `${item.name} ${item.version}`
    const lines = [heading]
    if (/^https?:\/\//i.test(item.homepage)) lines.push(item.homepage)
    if (item.license !== 'See included notices') lines.push(`License: ${item.license}`)
    if (item.noticeStatus === 'declaration-only') {
      lines.push('The upstream package does not include its full license text. See the project website for the complete terms.')
    }
    const seen = new Set<string>()
    for (const notice of item.notices) {
      for (const part of notice.parts) {
        // 同一组件内的重复文件也只显示一次，特有版权与 NOTICE 始终保留对应关系。
        if (seen.has(part.text)) continue
        seen.add(part.text)
        if ((uses.get(part.text) ?? 0) > 1) {
          if (!shared.has(part.text)) shared.set(part.text,
            part.kind === 'license' ? `L${++licenseNumber}` : `N${++noticeNumber}`)
          lines.push(`${part.kind === 'license' ? 'License' : 'Notice'} text: [${shared.get(part.text)}]`)
        } else {
          lines.push(texts[part.text])
        }
      }
    }
    return lines.join('\n\n')
  })
  if (shared.size) {
    sections.push('SHARED LICENSE TEXTS AND NOTICES\n\n' + [...shared].map(([id, label]) =>
      `[${label}]\n\n${texts[id]}`
    ).join('\n\n---\n\n'))
  }
  return sections.join('\n\n---\n\n')
}
