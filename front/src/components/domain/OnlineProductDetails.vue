<script setup lang="ts">
import { computed } from 'vue'
import type { OnlineListing } from '@/api/onlineProducts'
import { onlineAttributeName, onlineAttributeText } from './onlineAttributeDisplay'

const props = defineProps<{ listing: OnlineListing }>()
const issues = computed(() => props.listing.platform_issues || [])
const processing = computed(() => props.listing.platform === 'yandex' && props.listing.raw_sub_status.some(status =>
  status === 'HAS_CARD_CAN_UPDATE_PROCESSING' || status === 'NO_CARD_PROCESSING'))
const attributes = computed(() => props.listing.content.attributes || [])
</script>

<template>
  <section class="my-6 rounded-lg border border-accent-200 p-4 dark:border-dark-700" data-testid="platform-feedback">
    <h3 class="font-semibold">平台反馈</h3>
    <p v-if="processing" role="status" class="mt-2 text-sm text-primary-700">平台正在处理卡片更新，现有反馈可能来自上次处理；完成后请刷新状态。</p>
    <p class="muted mt-2">以下保留平台原文。销售状态与卡片修改状态分别展示，警告不代表商品一定不可售。</p>
    <ul v-if="issues.length" class="mt-3 space-y-3">
      <li v-for="(issue, index) in issues" :key="index" class="rounded-lg p-3" :class="issue.severity === 'error' ? 'bg-rose-50 text-rose-800 dark:bg-rose-950/30 dark:text-rose-200' : 'bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200'" :data-severity="issue.severity">
        <p class="whitespace-pre-wrap break-words font-medium">{{ issue.severity === 'error' ? '错误' : '警告' }}：{{ issue.message }}</p>
        <p v-if="issue.comment && issue.comment !== issue.message" class="mt-1 whitespace-pre-wrap break-words text-sm">{{ issue.comment }}</p>
        <p v-if="issue.code" class="mt-1 text-xs">平台代码：{{ issue.code }}</p>
      </li>
    </ul>
    <p v-else class="muted mt-3">当前记录没有具体错误或警告；反馈来自最近一次同步或状态查询。</p>
  </section>
  <section class="my-6" data-testid="online-attributes">
    <h3 class="font-semibold">商品属性</h3>
    <p v-if="listing.content.attribute_names_error" class="mt-2 text-sm text-amber-700">{{ listing.content.attribute_names_error }}；属性值仍保留，可重新同步后查看名称。</p>
    <dl v-if="attributes.length" class="mt-3 divide-y divide-accent-100 dark:divide-dark-700">
      <div v-for="attribute in attributes" :key="String(attribute.id)" class="grid gap-2 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <dt class="text-sm font-medium">{{ onlineAttributeName(attribute) }}<span class="muted mt-1 block text-xs">属性编号：{{ attribute.id }}</span></dt>
        <dd class="whitespace-pre-wrap break-words text-sm">{{ onlineAttributeText(attribute) }}</dd>
      </div>
    </dl>
    <p v-else class="muted mt-3">当前同步记录没有商品属性值。</p>
  </section>
</template>
