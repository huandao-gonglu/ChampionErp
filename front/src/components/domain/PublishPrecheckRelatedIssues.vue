<script setup lang="ts">
import type { PrecheckRelatedIssue } from '@/types/workflow'
import { precheckIssueIdentity } from '@/utils/precheckIssues'

defineProps<{ issues?: PrecheckRelatedIssue[] }>()
</script>

<template>
  <div v-if="issues?.length" class="mt-2 space-y-2 text-sm" aria-label="关联说明">
    <div v-for="issue in issues" :key="precheckIssueIdentity(issue)" class="border-l-2 border-current/30 pl-3">
      <p><span class="font-semibold">关联说明：</span>{{ issue.message }}</p>
      <p v-if="issue.nextAction" class="mt-1">{{ issue.nextAction }}</p>
    </div>
  </div>
</template>
