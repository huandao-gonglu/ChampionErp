<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { fetchImageBatch, type ImageBatchSnapshot } from '@/api/imageBatches'

const props = defineProps<{ conversationId: string; active?: boolean }>()
const emit = defineEmits<{ change: [batch: ImageBatchSnapshot | null] }>()
const batch = ref<ImageBatchSnapshot | null>(null)
const error = ref('')
let generation = 0
let timer: ReturnType<typeof setTimeout> | undefined

// 使用领域快照读取进度，不从模型文字或 SSE 结束事件推断批次完成。
async function refresh(id: string, requestGeneration: number) {
  try {
    const value = await fetchImageBatch(id)
    if (generation !== requestGeneration) return
    if (value && value.conversation_id !== id) throw new Error('返回的图片记录与当前任务不一致')
    batch.value = value
    error.value = ''
    emit('change', value)
  } catch (cause) {
    if (generation !== requestGeneration) return
    error.value = cause instanceof Error ? cause.message : '读取图片进度失败'
  }
  if (generation === requestGeneration && (error.value || (batch.value ? !batch.value.terminal : props.active))) {
    timer = setTimeout(() => { void refresh(id, requestGeneration) }, error.value ? 3000 : 1000)
  }
}

watch(() => [props.conversationId, props.active] as const, ([id], old) => {
  generation += 1
  clearTimeout(timer)
  if (id !== old?.[0]) {
    batch.value = null
    error.value = ''
    emit('change', null)
  }
  if (id && !id.startsWith('conversation_global_chat_')) void refresh(id, generation)
}, { immediate: true })

onBeforeUnmount(() => { generation += 1; clearTimeout(timer) })

const labels = { queued: '排队中', running: '处理中', success: '处理成功', failed: '处理失败' }
const icons = { queued: '○', running: '◌', success: '✓', failed: '✕' }
function safeUrl(url: string): boolean {
  return /^https?:\/\//i.test(url) || /^\/file\?path=/.test(url)
}
</script>

<template>
  <section v-if="batch" class="my-3 space-y-3 text-sm" aria-label="图片处理进度" data-testid="image-batch-progress">
    <p class="whitespace-pre-wrap text-slate-700 dark:text-accent-200">{{ batch.prompt }}</p>
    <div role="status" aria-live="polite">
      <p class="font-semibold">
        {{ batch.terminal ? '处理结束' : `已处理 ${batch.completed} / ${batch.total} 张` }}
        · 成功 {{ batch.success }} 张 · 失败 {{ batch.failed }} 张
      </p>
      <p v-if="!batch.terminal" class="mt-1 text-xs text-slate-500 dark:text-accent-300">
        处理中 {{ batch.running }} 张 · 排队中 {{ batch.queued }} 张
      </p>
    </div>
    <p v-if="error" role="alert" class="text-rose-600 dark:text-rose-300">进度暂时无法更新：{{ error }}</p>
    <ol class="space-y-2">
      <li v-for="item in batch.items" :key="item.index" :data-status="item.status" class="border-b border-slate-100 py-2 dark:border-dark-700">
        <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span aria-hidden="true" :class="{ 'text-emerald-600': item.status === 'success', 'text-rose-600': item.status === 'failed' }">{{ icons[item.status] }}</span>
          <span>第 {{ item.index + 1 }} 张：{{ item.outcome_unknown ? '结果待核对' : labels[item.status] }}</span>
          <a v-if="safeUrl(item.source_url)" :href="item.source_url" target="_blank" rel="noopener noreferrer" class="text-primary-600 underline dark:text-primary-300">查看原图</a>
          <template v-for="(url, index) in item.result_urls" :key="url">
            <a v-if="safeUrl(url)" :href="url" target="_blank" rel="noopener noreferrer" class="text-primary-600 underline dark:text-primary-300">查看结果图{{ item.result_urls.length > 1 ? ` ${index + 1}` : '' }}</a>
          </template>
        </div>
        <p v-if="item.error" class="mt-1 pl-5 text-xs text-rose-600 dark:text-rose-300">{{ item.error }}</p>
      </li>
    </ol>
  </section>
</template>
