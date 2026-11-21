<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  PhArrowRight,
  PhArrowsClockwise,
  PhArrowSquareOut,
  PhBrowsers,
  PhCheck,
  PhClock,
  PhCopy,
  PhFileHtml,
  PhFolderOpen,
  PhImage,
  PhImages,
  PhInfo,
  PhLightning,
  PhLink,
  PhPackage,
  PhPencilSimple,
  PhPlay,
  PhShieldWarning,
  PhSpinnerGap,
  PhTag,
  PhTrash,
  PhX,
  PhXCircle,
} from '@phosphor-icons/vue'
import { linkHistory, type CollectionLink, type LinkEvent } from '@/api/workflow/collectionLinks'
import { useClipboard } from '@/composables/useClipboard'
import { collectBatchStatusLabels } from '@/utils/collectQueue'
import { formatDateTime } from '@/utils/format'
import type { CollectBatchStatus } from '@/types/workflow'

const props = defineProps<{ row?: CollectionLink; busy: boolean; closable: boolean }>()
const emit = defineEmits<{ close: []; edit: []; remove: []; run: [row: CollectionLink]; openProduct: [id: string] }>()
const tab = ref<'result' | 'history'>('result')
const events = ref<LinkEvent[]>([])
const historyError = ref('')
const reading = ref(false)
const { copied, copy } = useClipboard()

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

function hostname(url: string) {
  try { return new URL(url).hostname } catch { return url }
}
function eventDotClass(status: CollectBatchStatus) {
  if (status === 'success') return 'bg-primary-500 ring-primary-100 dark:ring-primary-500/20'
  if (status === 'failed') return 'bg-rose-500 ring-rose-100 dark:ring-rose-500/20'
  if (status === 'running') return 'bg-blue-500 ring-blue-100 dark:ring-blue-500/20'
  if (status === 'waiting_verification' || status === 'unconfirmed') return 'bg-amber-500 ring-amber-100 dark:ring-amber-500/20'
  return 'bg-accent-400 ring-accent-100 dark:ring-dark-700'
}
</script>

<template>
  <aside class="min-w-0">
    <!-- 顶部标题与工具图标 -->
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-base font-bold text-accent-950 dark:text-white">链接详情</h2>
      <div v-if="row" class="flex items-center gap-1.5">
        <button
          type="button"
          aria-label="编辑链接"
          title="编辑链接"
          class="detail-icon"
          :disabled="busy || row.status === 'running'"
          @click="emit('edit')"
        >
          <PhPencilSimple :size="15" aria-hidden="true" />
        </button>
        <button
          type="button"
          aria-label="移除链接"
          title="移除链接"
          class="detail-icon hover:!border-rose-200 hover:!bg-rose-50 hover:!text-rose-600 dark:hover:!border-rose-500/30 dark:hover:!bg-rose-500/10 dark:hover:!text-rose-300"
          :disabled="busy || row.status === 'running'"
          @click="emit('remove')"
        >
          <PhTrash :size="15" aria-hidden="true" />
        </button>
        <button
          v-if="closable"
          type="button"
          aria-label="关闭详情"
          title="关闭详情"
          class="detail-icon"
          @click="emit('close')"
        >
          <PhX :size="15" aria-hidden="true" />
        </button>
      </div>
    </div>

    <div v-if="!row" class="my-10 flex flex-col items-center justify-center gap-2 text-center">
      <div class="grid size-11 place-items-center rounded-2xl bg-accent-100 text-accent-400 dark:bg-dark-800">
        <PhLink :size="20" aria-hidden="true" />
      </div>
      <p class="muted">选择一条链接查看采集详情。</p>
    </div>

    <template v-else>
      <!-- 身份标签与状态 -->
      <div class="mt-2.5 flex flex-wrap items-center gap-1.5 text-xs">
        <span
          class="max-w-full rounded-md border border-primary-300/60 bg-primary-50/70 px-2 py-0.5 font-semibold text-primary-800 [overflow-wrap:anywhere] dark:border-primary-500/30 dark:bg-primary-500/15 dark:text-primary-200"
        >
          {{ row.platform === 'generic' ? hostname(row.url) : row.platform }}
        </span>
        <span class="inline-flex items-center gap-1 rounded-md bg-accent-100 px-2 py-0.5 font-medium text-accent-700 dark:bg-dark-700 dark:text-accent-200">
          <PhLightning v-if="row.mode === 'api'" :size="12" aria-hidden="true" />
          <PhBrowsers v-else :size="12" aria-hidden="true" />
          {{ row.mode === 'api' ? 'API 采集' : '浏览器采集' }}
        </span>
        <span class="detail-status" :data-status="row.status">
          {{ collectBatchStatusLabels[row.status] }}
        </span>
      </div>

      <!-- URL 链接卡 + 一键复制 -->
      <div class="mt-2.5 rounded-xl border border-accent-200/90 bg-white p-3 shadow-sm dark:border-dark-700 dark:bg-dark-800/80">
        <div class="flex items-start gap-2">
          <a
            :href="row.url"
            target="_blank"
            rel="noopener noreferrer"
            class="group flex min-w-0 flex-1 items-start gap-2 text-xs leading-relaxed text-accent-700 hover:text-primary-700 dark:text-accent-200 dark:hover:text-primary-300"
          >
            <PhLink :size="15" class="mt-0.5 shrink-0 text-accent-400 group-hover:text-primary-600 dark:group-hover:text-primary-400" aria-hidden="true" />
            <span class="min-w-0 grow break-all font-mono group-hover:underline">{{ row.url }}</span>
            <PhArrowSquareOut :size="14" class="mt-0.5 shrink-0 opacity-60 group-hover:opacity-100" aria-hidden="true" />
          </a>
          <button
            type="button"
            class="grid size-7 shrink-0 place-items-center rounded-lg border border-accent-200 text-accent-500 transition hover:bg-accent-100 hover:text-accent-900 dark:border-dark-600 dark:text-accent-300 dark:hover:bg-dark-700 dark:hover:text-white"
            :aria-label="copied ? '已复制链接' : '复制链接'"
            :title="copied ? '已复制' : '复制链接'"
            @click="copy(row.url)"
          >
            <PhCheck v-if="copied" :size="14" class="text-primary-600 dark:text-primary-400" aria-hidden="true" />
            <PhCopy v-else :size="14" aria-hidden="true" />
          </button>
        </div>
        <div v-if="row.createdAt || row.updatedAt" class="mt-2 space-y-0.5 border-t border-accent-100 pt-2 text-[11px] tabular-nums text-accent-500 dark:border-dark-700/80 dark:text-accent-400">
          <div v-if="row.createdAt" class="flex items-center justify-between gap-2">
            <span class="inline-flex items-center gap-1"><PhClock :size="11" aria-hidden="true" />添加时间</span>
            <span>{{ formatDateTime(row.createdAt) }}</span>
          </div>
          <div v-if="row.updatedAt" class="flex items-center justify-between gap-2">
            <span>最近更新</span>
            <span>{{ formatDateTime(row.updatedAt) }}</span>
          </div>
        </div>
      </div>

      <!-- 置顶主操作区：无需向下滚动即可直接触发采集/重试/查看商品 -->
      <div class="mt-3 flex flex-wrap items-center gap-2">
        <button
          v-if="row.productId"
          type="button"
          class="btn btn-primary !min-h-9 flex-1 !rounded-xl !px-3.5 !py-1.5 text-sm"
          :disabled="busy"
          @click="emit('openProduct', row.productId)"
        >
          查看商品 <PhArrowRight :size="15" aria-hidden="true" />
        </button>
        <button
          v-if="row.status === 'pending'"
          type="button"
          class="btn btn-primary !min-h-9 flex-1 !rounded-xl !px-3.5 !py-1.5 text-sm"
          :disabled="busy"
          @click="emit('run', row)"
        >
          <PhPlay :size="14" weight="fill" aria-hidden="true" />立即采集
        </button>
        <button
          v-else-if="row.status === 'waiting_verification'"
          type="button"
          class="btn btn-primary !min-h-9 flex-1 !rounded-xl !px-3.5 !py-1.5 text-sm"
          :disabled="busy"
          @click="emit('run', row)"
        >
          <PhPlay :size="14" weight="fill" aria-hidden="true" />继续验证采集
        </button>
        <button
          v-else-if="row.status !== 'running'"
          type="button"
          class="btn !min-h-9 !rounded-xl !px-3.5 !py-1.5 text-sm"
          :class="row.productId ? 'btn-outline' : 'btn-primary flex-1'"
          :disabled="busy"
          @click="emit('run', row)"
        >
          <PhArrowsClockwise :size="15" aria-hidden="true" />重新采集
        </button>
        <span
          v-else
          class="inline-flex min-h-9 flex-1 items-center justify-center gap-2 rounded-xl border border-blue-200 bg-blue-50/80 px-3 text-xs font-semibold text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-300"
        >
          <PhSpinnerGap :size="14" class="animate-spin" aria-hidden="true" />正在采集中…
        </span>
      </div>

      <!-- 标签切换 -->
      <div class="mt-3.5 flex gap-5 border-b border-accent-200/80 text-sm dark:border-dark-700">
        <button type="button" class="detail-tab" :aria-pressed="tab === 'result'" @click="tab = 'result'">采集结果</button>
        <button type="button" class="detail-tab" :aria-pressed="tab === 'history'" @click="tab = 'history'">诊断日志</button>
      </div>

      <!-- 采集结果 -->
      <section v-if="tab === 'result'" class="space-y-3.5 py-3.5">
        <!-- 商品摘要卡 -->
        <div
          v-if="row.title || row.image"
          class="flex items-start gap-3 rounded-xl border border-accent-200/90 bg-white p-3 shadow-sm dark:border-dark-700 dark:bg-dark-800/80"
        >
          <a
            v-if="row.image"
            :href="row.image"
            target="_blank"
            rel="noopener noreferrer"
            title="点击查看商品主图"
            class="shrink-0 overflow-hidden rounded-xl border border-accent-200/80 transition hover:opacity-90 dark:border-dark-600"
          >
            <img :src="row.image" alt="采集商品" class="size-16 object-cover" />
          </a>
          <div class="min-w-0 flex-1">
            <h3 class="break-words text-sm font-semibold leading-snug text-accent-950 dark:text-white">{{ row.title || hostname(row.url) }}</h3>
            <span
              v-if="row.partial"
              class="mt-1.5 inline-flex items-center rounded-md bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300"
            >
              已取得部分商品资料
            </span>
          </div>
        </div>

        <!-- 结构化指标网格 -->
        <dl v-if="row.productId || row.skuCount !== undefined" class="grid grid-cols-2 gap-2 text-sm">
          <div class="metric-card">
            <dt class="metric-label"><PhTag :size="13" aria-hidden="true" />来源价格</dt>
            <dd class="metric-value tabular-nums text-primary-700 dark:text-primary-300">
              {{ row.price ? [row.currency, row.price].filter(Boolean).join(' ') : '—' }}
            </dd>
          </div>
          <div class="metric-card">
            <dt class="metric-label"><PhPackage :size="13" aria-hidden="true" />SKU 数量</dt>
            <dd class="metric-value tabular-nums">{{ row.skuCount ?? '—' }}</dd>
          </div>
          <div class="metric-card">
            <dt class="metric-label"><PhImages :size="13" aria-hidden="true" />图片数量</dt>
            <dd class="metric-value tabular-nums">{{ row.imageCount ?? '—' }}</dd>
          </div>
          <div class="metric-card">
            <dt class="metric-label"><PhFolderOpen :size="13" aria-hidden="true" />保存位置</dt>
            <dd class="metric-value">采集箱</dd>
          </div>
        </dl>

        <!-- 未采集 / 采集中 / 待验证 状态引导卡 -->
        <div
          v-else-if="row.status === 'pending'"
          class="rounded-xl border border-dashed border-accent-300 bg-white/80 p-4 text-center dark:border-dark-600 dark:bg-dark-800/40"
        >
          <p class="text-sm font-semibold text-accent-800 dark:text-accent-100">尚未开始采集</p>
          <p class="mt-1 text-xs leading-relaxed text-accent-500 dark:text-accent-400">
            开始采集后，这里会显示该链接的商品资料、来源价格、SKU 与图片统计。
          </p>
        </div>

        <div
          v-else-if="row.status === 'running'"
          role="status"
          class="flex items-start gap-3 rounded-xl border border-blue-200 bg-blue-50/80 p-3.5 text-sm text-blue-900 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200"
        >
          <PhSpinnerGap :size="18" class="mt-0.5 shrink-0 animate-spin text-blue-600 dark:text-blue-400" aria-hidden="true" />
          <div>
            <p class="font-semibold">正在采集这个链接…</p>
            <p class="mt-0.5 text-xs opacity-80">正在读取商品信息与图片素材，请稍候。</p>
          </div>
        </div>

        <div
          v-else-if="row.status === 'waiting_verification'"
          class="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50/90 p-3.5 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
        >
          <PhShieldWarning :size="18" weight="fill" class="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden="true" />
          <div>
            <p class="font-semibold">等待商品页完成验证</p>
            <p class="mt-0.5 text-xs opacity-85">请在商品页完成登录或人机验证，或点击上方按钮继续检查。</p>
          </div>
        </div>

        <!-- 错误与建议操作 -->
        <div
          v-if="row.error"
          class="space-y-2 rounded-xl border border-rose-200/90 bg-rose-50/90 p-3 text-sm text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
        >
          <div class="flex items-start gap-2">
            <PhXCircle :size="17" weight="fill" class="mt-0.5 shrink-0 text-rose-500 dark:text-rose-400" aria-hidden="true" />
            <p class="min-w-0 break-words leading-relaxed">{{ row.error }}</p>
          </div>
          <code v-if="row.errorCode" class="inline-block rounded-md bg-rose-100/90 px-2 py-0.5 text-[11px] font-medium text-rose-700 dark:bg-rose-500/20 dark:text-rose-200">
            {{ row.errorCode }}
          </code>
        </div>

        <div
          v-if="row.nextAction"
          class="flex items-start gap-2 rounded-xl border border-amber-200/80 bg-amber-50/70 p-3 text-xs leading-relaxed text-amber-900 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200"
        >
          <PhInfo :size="15" class="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden="true" />
          <p class="min-w-0 break-words"><strong class="font-semibold">建议操作：</strong>{{ row.nextAction }}</p>
        </div>
      </section>

      <!-- 诊断日志 -->
      <section v-else class="space-y-3.5 py-3.5 text-sm" aria-live="polite">
        <p v-if="historyError" role="alert" class="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
          {{ historyError }}
        </p>
        <div v-else-if="reading" class="flex items-center gap-2 py-4 text-xs text-accent-500 dark:text-accent-400">
          <PhSpinnerGap :size="15" class="animate-spin text-primary-500" aria-hidden="true" />正在读取日志…
        </div>
        <p v-else-if="!events.length" class="muted py-4 text-center text-xs">暂无诊断日志记录。</p>

        <ol v-else class="relative space-y-2.5 pl-5 before:absolute before:bottom-2 before:left-[7px] before:top-2 before:w-px before:bg-accent-200 dark:before:bg-dark-700">
          <li v-for="(event, index) in events" :key="index" class="relative rounded-xl border border-accent-200/80 bg-white p-2.5 dark:border-dark-700 dark:bg-dark-800/70">
            <span
              class="absolute -left-[18px] top-3.5 size-2.5 rounded-full ring-4"
              :class="eventDotClass(event.status)"
              aria-hidden="true"
            />
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="detail-status !px-2 !py-0.5 !text-[11px]" :data-status="event.status">
                {{ collectBatchStatusLabels[event.status] }}
              </span>
              <time class="text-[11px] tabular-nums text-accent-500 dark:text-accent-400">
                {{ new Date(event.at).toLocaleString('zh-CN') }}
              </time>
            </div>
            <p class="mt-1.5 break-words text-xs leading-relaxed text-accent-800 dark:text-accent-100">
              {{ event.message || collectBatchStatusLabels[event.status] }}
            </p>
            <code
              v-if="event.code"
              class="mt-1.5 inline-block break-all rounded bg-accent-100 px-1.5 py-0.5 text-[11px] text-accent-600 dark:bg-dark-900 dark:text-accent-300"
            >
              {{ event.code }}
            </code>
          </li>
        </ol>

        <div v-if="row.screenshot || row.snapshot" class="flex flex-wrap gap-2 border-t border-accent-200/80 pt-3 dark:border-dark-700">
          <a
            v-if="row.screenshot"
            :href="row.screenshot"
            target="_blank"
            rel="noopener"
            class="btn btn-outline !min-h-8 !rounded-xl !px-3 !py-1.5 text-xs"
          >
            <PhImage :size="14" aria-hidden="true" />打开截图
          </a>
          <a
            v-if="row.snapshot"
            :href="row.snapshot"
            target="_blank"
            rel="noopener"
            class="btn btn-outline !min-h-8 !rounded-xl !px-3 !py-1.5 text-xs"
          >
            <PhFileHtml :size="14" aria-hidden="true" />打开 HTML 快照
          </a>
        </div>
      </section>
    </template>
  </aside>
</template>

<style scoped>
.detail-icon {
  @apply grid size-8 shrink-0 place-items-center rounded-xl border border-accent-200 bg-white text-accent-600 transition hover:bg-accent-100 hover:text-accent-950 disabled:cursor-not-allowed disabled:opacity-40 dark:border-dark-700 dark:bg-dark-800 dark:text-accent-300 dark:hover:bg-dark-700 dark:hover:text-white;
}
.detail-tab {
  @apply border-b-2 border-transparent pb-2 text-xs font-medium text-accent-600 transition hover:text-accent-950 dark:text-accent-300 dark:hover:text-white;
}
.detail-tab[aria-pressed='true'] {
  @apply border-primary-500 font-semibold text-primary-700 dark:text-primary-300;
}
.metric-card {
  @apply rounded-xl border border-accent-200/80 bg-white px-3 py-2 dark:border-dark-700 dark:bg-dark-800/80;
}
.metric-label {
  @apply flex items-center gap-1 text-[11px] font-medium text-accent-500 dark:text-accent-400;
}
.metric-value {
  @apply mt-0.5 truncate text-sm font-bold text-accent-950 dark:text-white;
}
.detail-status {
  @apply inline-flex items-center rounded-full bg-accent-100 px-2.5 py-0.5 text-xs font-medium text-accent-700 dark:bg-dark-700 dark:text-accent-200;
}
.detail-status[data-status='success'] {
  @apply bg-primary-100 text-primary-800 dark:bg-primary-500/15 dark:text-primary-300;
}
.detail-status[data-status='failed'] {
  @apply bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300;
}
.detail-status[data-status='running'] {
  @apply bg-blue-100 text-blue-800 dark:bg-blue-500/15 dark:text-blue-300;
}
.detail-status[data-status='waiting_verification'],
.detail-status[data-status='unconfirmed'] {
  @apply bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300;
}
</style>
