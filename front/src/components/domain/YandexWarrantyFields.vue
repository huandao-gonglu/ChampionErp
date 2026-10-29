<script setup lang="ts">
import { computed } from 'vue'
import type { DraftDetail, PrecheckIssue } from '@/types/workflow'
import YandexGuaranteePeriodFields from '@/components/shared/YandexGuaranteePeriodFields.vue'

const props = defineProps<{ draft: DraftDetail; issues: PrecheckIssue[]; loading?: boolean }>()
const emit = defineEmits<{ invalidatePublishValidation: [] }>()
const period = computed({
  get: () => props.draft.guaranteePeriod || {},
  set: value => { props.draft.guaranteePeriod = value; emit('invalidatePublishValidation') },
})
const issues = computed(() => props.issues.filter(issue => issue.field === 'guarantee_period'))
</script>

<template>
  <fieldset :disabled="loading" class="mb-3 min-w-0 rounded-lg border border-accent-200 bg-white p-4 dark:border-dark-700 dark:bg-dark-900/80" data-testid="yandex-warranty-fields">
    <h3 class="text-sm font-semibold text-accent-950 dark:text-white">Yandex 保修期限</h3>
    <p class="mt-1 text-xs text-accent-500 dark:text-accent-400">按实际售后承诺填写保修时长；保修说明请使用销售市场的语言。</p>
    <YandexGuaranteePeriodFields v-model="period" />
    <p v-for="issue in issues" :key="issue.code" class="mt-2 text-xs text-rose-700">{{ issue.message }}</p>
  </fieldset>
</template>
