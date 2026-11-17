<script setup lang="ts">
import { ref, watch } from 'vue'
import { PhArrowSquareOut, PhLink, PhPencilSimple, PhTrash } from '@phosphor-icons/vue'
import { linkHistory, type CollectionLink, type LinkEvent } from '@/api/workflow/collectionLinks'
import { collectBatchStatusLabels } from '@/utils/collectQueue'
const props = defineProps<{ row?: CollectionLink; busy: boolean }>()
const emit = defineEmits<{ edit: []; remove: []; openProduct: [id: string] }>()
const tab = ref<'result' | 'history'>('result')
const events = ref<LinkEvent[]>([])
const historyError = ref('')
const reading = ref(false)
watch(() => props.row?.id, () => { tab.value = 'result' })
watch([() => props.row?.id, () => props.row?.revision, tab], async ([id, , section], _, onCleanup) => {
  let active = true
  onCleanup(() => { active = false })
  events.value = []
  historyError.value = ''
  if (!id || section !== 'history') return
  reading.value = true
  try {
    const items = await linkHistory(id)
    if (active) events.value = items
  } catch (e) { if (active) historyError.value = (e as Error).message }
  finally { if (active) reading.value = false }
})
function hostname(url: string) { return new URL(url).hostname }
</script>

<template>
  <aside class="min-w-0 border-t border-slate-200 pt-5 dark:border-dark-700 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-lg font-bold">链接详情</h2>
      <div v-if="row" class="flex gap-1">
        <button aria-label="编辑链接" title="编辑链接" class="detail-icon" :disabled="busy || row.status === 'running'" @click="emit('edit')"><PhPencilSimple /></button>
        <button aria-label="移除链接" title="移除链接" class="detail-icon" :disabled="busy || row.status === 'running'" @click="emit('remove')"><PhTrash /></button>
      </div>
    </div>
    <p v-if="!row" class="muted my-6">选择一条链接查看采集详情。</p>
    <template v-else>
      <div class="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <span class="rounded border border-emerald-400/40 px-2 py-1 text-emerald-700 dark:text-emerald-300">{{ row.platform === 'generic' ? hostname(row.url) : row.platform }}</span>
        <span>{{ row.mode === 'api' ? 'API 采集' : '浏览器采集' }}</span><span aria-hidden="true">·</span><span>{{ collectBatchStatusLabels[row.status] }}</span>
      </div>
      <a :href="row.url" target="_blank" rel="noopener noreferrer" class="my-4 flex items-start gap-2 rounded-lg border border-slate-200 p-3 text-sm hover:underline dark:border-dark-700"><PhLink class="mt-0.5 shrink-0" /><span class="min-w-0 grow break-all">{{ row.url }}</span><PhArrowSquareOut class="shrink-0" /></a>
      <div class="flex gap-5 border-b border-slate-200 text-sm dark:border-dark-700">
        <button class="detail-tab" :aria-pressed="tab === 'result'" @click="tab = 'result'">采集结果</button>
        <button class="detail-tab" :aria-pressed="tab === 'history'" @click="tab = 'history'">诊断日志</button>
      </div>
      <section v-if="tab === 'result'" class="space-y-4 py-4">
        <div v-if="row.title || row.image" class="flex items-center gap-3">
          <img v-if="row.image" :src="row.image" alt="采集商品" class="size-16 rounded-lg object-cover" />
          <h3 class="break-words font-semibold">{{ row.title }}</h3>
        </div>
        <dl v-if="row.productId || row.skuCount !== undefined" class="divide-y divide-slate-200 text-sm dark:divide-dark-700">
          <div v-for="[name, value] in [['来源价格', row.price ? [row.currency, row.price].filter(Boolean).join(' ') : '—'], ['SKU 数量', row.skuCount ?? '—'], ['图片数量', row.imageCount ?? '—'], ['保存位置', '采集箱']]" :key="String(name)" class="flex justify-between gap-2 py-2"><dt class="text-slate-500 dark:text-accent-300">{{ name }}</dt><dd>{{ value }}</dd></div>
        </dl>
        <p v-else-if="row.status === 'pending'" class="muted">开始采集后，这里会显示该链接的商品资料。</p>
        <p v-else-if="row.status === 'running'" role="status" class="muted">正在采集这个链接…</p>
        <p v-if="row.partial" class="muted">已取得部分商品资料。</p>
        <p v-if="row.error" class="break-words rounded-lg bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-200">{{ row.error }}</p>
        <p v-if="row.nextAction" class="muted">{{ row.nextAction }}</p>
        <button v-if="row.productId" class="btn btn-primary" :disabled="busy" @click="emit('openProduct', row.productId)">查看商品</button>
      </section>
      <section v-else class="space-y-3 py-4 text-sm" aria-live="polite">
        <p v-if="historyError" role="alert">{{ historyError }}</p><p v-else-if="reading" class="muted">正在读取日志…</p>
        <ol class="space-y-3">
          <li v-for="(event, index) in events" :key="index" class="border-l-2 border-emerald-500/50 pl-3">
            <time class="text-xs text-slate-500">{{ new Date(event.at).toLocaleString('zh-CN') }}</time>
            <p class="mt-1 break-words">{{ event.message || collectBatchStatusLabels[event.status] }}</p><code v-if="event.code" class="break-all text-xs">{{ event.code }}</code>
          </li>
        </ol>
        <div class="flex gap-3"><a v-if="row.screenshot" :href="row.screenshot" target="_blank" rel="noopener" class="underline">打开截图</a><a v-if="row.snapshot" :href="row.snapshot" target="_blank" rel="noopener" class="underline">打开 HTML 快照</a></div>
      </section>
    </template>
  </aside>
</template>
<style scoped>
.detail-icon { @apply grid size-8 place-items-center rounded-md border border-slate-200 disabled:opacity-40 dark:border-dark-700; }
.detail-tab { @apply border-b-2 border-transparent pb-2; }
.detail-tab[aria-pressed=true] { @apply border-emerald-500 font-semibold text-emerald-700 dark:text-emerald-300; }
</style>
