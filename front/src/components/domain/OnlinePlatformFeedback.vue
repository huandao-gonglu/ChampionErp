<script setup lang="ts">
import { computed } from 'vue'
import type { OnlineListing } from '@/api/onlineProducts'

const props = defineProps<{ listing: OnlineListing }>()
const issues = computed(() => props.listing.platform_issues || [])
const processing = computed(() => props.listing.platform === 'yandex' && props.listing.raw_sub_status.some(status =>
  status === 'HAS_CARD_CAN_UPDATE_PROCESSING' || status === 'NO_CARD_PROCESSING'))
</script>

<template>
  <section v-if="issues.length || processing" class="my-5 space-y-3" data-testid="platform-feedback">
    <h3 v-if="issues.length" class="text-sm font-semibold">平台反馈 · {{ issues.length }} 条</h3>
    <p v-if="processing" role="status" class="rounded-lg bg-primary-50 p-3 text-sm text-primary-800 dark:bg-primary-900/20 dark:text-primary-200">平台正在处理卡片更新，现有反馈可能来自上次处理；完成后请刷新状态。</p>
    <ul v-if="issues.length" class="space-y-2">
      <li v-for="(issue, index) in issues" :key="index" class="rounded-lg p-3" :class="issue.severity === 'error' ? 'bg-rose-50 text-rose-800 dark:bg-rose-950/30 dark:text-rose-200' : 'bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200'" :data-severity="issue.severity">
        <p class="whitespace-pre-wrap break-words text-sm font-medium">{{ issue.severity === 'error' ? '错误' : '警告' }}：{{ issue.message }}</p>
        <p v-if="issue.comment && issue.comment !== issue.message" class="mt-1 whitespace-pre-wrap break-words text-sm">{{ issue.comment }}</p>
      </li>
    </ul>
  </section>
</template>
