<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import noticesUrl from '@/legal/third-party-notices.json?url'

type NoticePackage = {
  name: string
  version: string
  ecosystem: string
  license: string
  homepage: string
  noticeStatus: 'included' | 'declaration-only'
  notices: Array<{ source: string; text: string }>
}
type NoticeCatalog = { packages: NoticePackage[]; texts: Record<string, string> }
const catalog = shallowRef<NoticeCatalog | null>(null)
const loading = ref(false)
const failed = ref(false)
let request: AbortController | undefined
const noticeText = computed(() => {
  if (!catalog.value) return ''
  const { packages, texts } = catalog.value
  const entries = [...packages].sort((left, right) =>
    left.name.localeCompare(right.name, 'en', { sensitivity: 'base' }) ||
    left.version.localeCompare(right.version, 'en') ||
    left.ecosystem.localeCompare(right.ecosystem, 'en')
  )
  return entries.map(item => {
    const heading = `The following software may be included in this product: ${item.name} (${item.version})`
    const notices = item.notices.map(notice => texts[notice.text]).join('\n\n')
    const declaration = item.noticeStatus === 'declaration-only'
      ? `Declared license: ${item.license}\n\nThe published package does not include its full license text. Any supplied attribution is reproduced below. Refer to the upstream project for the complete terms.${item.homepage ? `\nProject website: ${item.homepage}` : ''}`
      : 'This software contains the following license and notice below:'
    return `${heading}\n${declaration}${notices ? `\n\n${notices}` : ''}`
  }).join('\n\n---\n\n')
})
async function load() {
  request?.abort()
  const controller = new AbortController()
  request = controller
  loading.value = true
  failed.value = false
  try {
    const response = await fetch(noticesUrl, { signal: controller.signal })
    if (!response.ok) throw new Error('Notices unavailable')
    const data = await response.json() as NoticeCatalog
    if (!Array.isArray(data.packages) || !data.texts) throw new Error('Invalid notices')
    if (controller.signal.aborted) return
    catalog.value = data
  } catch {
    if (!controller.signal.aborted) failed.value = true
  } finally {
    if (!controller.signal.aborted) loading.value = false
  }
}
onMounted(load)
onBeforeUnmount(() => request?.abort())
</script>

<template>
  <div lang="en" class="space-y-5">
    <p class="text-sm leading-6 text-accent-600 dark:text-accent-300">THE FOLLOWING SETS FORTH ATTRIBUTION NOTICES FOR THIRD PARTY SOFTWARE THAT MAY BE CONTAINED IN PORTIONS OF THIS PRODUCT OR USED TO BUILD AND TEST IT.</p>
    <p v-if="loading" role="status">Loading third-party notices…</p>
    <div v-else-if="failed" role="alert" class="space-y-3">
      <p>Unable to load third-party notices. Please try again.</p>
      <button type="button" class="btn btn-outline" @click="load">Retry</button>
    </div>
    <div v-else-if="catalog" class="min-w-0 rounded-xl border border-accent-200 bg-accent-50 p-5 dark:border-dark-700 dark:bg-dark-800/70 sm:p-6">
      <pre class="whitespace-pre-wrap break-words font-mono text-sm leading-7 [overflow-wrap:anywhere]" aria-label="Third-party license notices">{{ noticeText }}</pre>
    </div>
  </div>
</template>
