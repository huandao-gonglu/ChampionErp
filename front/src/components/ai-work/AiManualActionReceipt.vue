<script setup lang="ts">
import { computed } from 'vue'
import type { ManualAiAction } from '@/types/aiWork'
import AiImageBatchProgress from './AiImageBatchProgress.vue'

const props = defineProps<{ action: ManualAiAction }>()
const labels: Record<ManualAiAction['state'], string> = {
  running: '处理中',
  waiting: '已提交',
  completed: '已完成',
  failed: '执行失败',
  partial: '部分完成',
  needs_input: '需要确认',
  cancelled: '已停止',
  unknown: '结果待核对',
}
const scope = computed(() => [props.action.scope?.platform, props.action.scope?.site, props.action.scope?.target_language]
  .filter((value): value is string => typeof value === 'string' && !!value).join(' · '))
const details = computed(() => props.action.result && Object.keys(props.action.result).length
  ? JSON.stringify(props.action.result, null, 2) : '')
</script>

<template>
  <section class="rounded-xl border border-slate-200 p-3 text-sm dark:border-dark-700" data-testid="manual-ai-action">
    <p v-if="action.job?.kind !== 'image_batch'" class="font-semibold" role="status">
      {{ labels[action.state] }}<span v-if="scope" class="ml-2 font-normal text-slate-500">{{ scope }}</span>
    </p>
    <p v-if="action.message" class="mt-1 whitespace-pre-wrap break-words">{{ action.message }}</p>
    <AiImageBatchProgress v-if="action.job?.kind === 'image_batch'" :conversation-id="action.job.id" active />
    <details v-if="details" class="mt-2">
      <summary class="cursor-pointer text-primary-600 dark:text-primary-300">查看结果</summary>
      <pre class="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">{{ details }}</pre>
    </details>
  </section>
</template>
