<script setup lang="ts">
import { computed } from 'vue'
import type { PublishPreparationState } from '@/types/workflow'

const props = defineProps<{ progress: PublishPreparationState }>()
const labels: Record<PublishPreparationState['stage'], string> = {
  saving_draft: '正在保存草稿…',
  validating_source: '正在检查图片来源和发布资料…',
  uploading: '正在上传并核对图片…',
  saving_images: '正在保存图片准备结果…',
  validating_final: '正在执行最终发布校验…',
  building_preview: '正在生成发布预览…',
  saving_preview: '正在保存发布预览…',
  completed: '素材与发布预览已就绪',
  failed: '准备未完成，请查看错误后重试',
}
const percent = computed(() => props.progress.total
  ? Math.floor((props.progress.completed ?? 0) * 100 / props.progress.total) : null)
</script>

<template>
  <div class="min-w-64 space-y-2 text-sm text-accent-700 dark:text-accent-200" role="status" aria-live="polite" data-testid="publish-preparation-progress">
    <p>{{ labels[progress.stage] }}</p>
    <template v-if="percent !== null">
      <p>图片已准备 {{ progress.completed }} / {{ progress.total }} 张（{{ percent }}%）</p>
      <div
        class="h-2 overflow-hidden rounded-full bg-accent-100 dark:bg-dark-700"
        role="progressbar" aria-label="图片准备进度" :aria-valuenow="progress.completed" :aria-valuemax="progress.total" :aria-valuemin="0"
      >
        <div class="h-full rounded-full transition-[width]" :class="progress.stage === 'failed' ? 'bg-rose-500' : 'bg-brand-600'" :style="{ width: `${percent}%` }" />
      </div>
      <p class="muted text-xs">包含已上传图片的复用检查</p>
    </template>
  </div>
</template>
