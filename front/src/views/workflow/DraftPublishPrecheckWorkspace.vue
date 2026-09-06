<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import PublishPrecheckPanel from '@/components/domain/PublishPrecheckPanel.vue'
import PublishDraftFields from '@/components/domain/PublishDraftFields.vue'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowActivityStore } from '@/stores/workflow/activity'
import { useWorkflowPublishingStore } from '@/stores/workflow/publishing'

const { targetEditors, publishBatch, currentDraft } = storeToRefs(useWorkflowStore())
const { platformOptions } = storeToRefs(useWorkflowPublishingStore())
const { loading } = storeToRefs(useWorkflowActivityStore())
const sharedIssues = computed(() => targetEditors.value.flatMap((editor) => editor.state.precheck?.errorItems || []))
const readyNames = computed(() => publishBatch.value.ready.map((editor) => {
  const platform = platformOptions.value.find((item) => item.key === editor.target.platform)?.label || editor.target.platform
  return `${platform} · ${editor.target.site}`
}).join('、'))

function invalidateSharedValidation() {
  for (const editor of targetEditors.value) editor.actions.invalidatePublishValidation()
}
</script>

<template>
  <div class="space-y-5">
    <section class="rounded-lg border border-accent-200 bg-white p-5 dark:border-dark-700 dark:bg-dark-900/80" data-testid="publish-batch-actions">
      <h2 class="card-title">发布预检</h2>
      <p class="muted mt-1">统一检查所有市场、准备发布预览，再一次确认加入队列。未通过的市场保留问题清单。</p>
      <div class="mt-4 flex flex-wrap gap-3">
        <button class="btn btn-outline" :disabled="loading || !publishBatch.pending.length" @click="publishBatch.precheckAll">全部预检</button>
        <button class="btn btn-outline" :disabled="loading || !publishBatch.passed.length" @click="publishBatch.previewAll">准备发布预览（{{ publishBatch.passed.length }}）</button>
        <button class="btn btn-primary" :disabled="loading || !publishBatch.ready.length" @click="publishBatch.enqueueAll">确认发布已就绪市场（{{ publishBatch.ready.length }}）</button>
      </div>
      <p class="muted mt-3">共 {{ targetEditors.length }} 个目标 · 待处理 {{ publishBatch.pending.length }} 个 · 预检通过 {{ publishBatch.passed.length }} 个 · 已就绪 {{ publishBatch.ready.length }} 个 · 已入队 {{ publishBatch.queuedCount }} 个</p>
      <p v-if="readyNames" class="mt-2 text-sm text-accent-700 dark:text-accent-200">本次确认将提交：{{ readyNames }}。请核对下方各市场的发布摘要。</p>
      <p v-if="publishBatch.progress.message" class="muted mt-2" role="status">{{ publishBatch.progress.message }}<span v-if="publishBatch.progress.operation">（{{ publishBatch.progress.completed }} / {{ publishBatch.progress.total }}）</span></p>
      <p v-if="publishBatch.progress.error" class="mt-2 text-sm text-rose-700 dark:text-rose-300" role="alert">{{ publishBatch.progress.error }}</p>
    </section>
    <fieldset :disabled="loading" class="min-w-0">
      <PublishDraftFields :draft="currentDraft" :issues="sharedIssues" @invalidate-publish-validation="invalidateSharedValidation" />
    </fieldset>
    <div class="grid items-start gap-6" :class="targetEditors.length > 1 ? 'xl:grid-cols-2' : ''">
      <section
        v-for="editor in targetEditors"
        :key="editor.key"
        :aria-label="`${editor.target.platform} 发布预检`"
        class="relative min-w-0"
        data-testid="publish-target-editor"
      >
        <p v-if="editor.state.queuedPublishJobId" class="mb-3 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-200" role="status">已加入发布队列 · {{ editor.state.queuedPublishJobId }}</p>
        <div v-else-if="editor.state.publishFailure" class="mb-3 rounded-lg bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300" role="alert">
          <p class="whitespace-pre-line break-words">{{ editor.state.publishFailure.message }}</p>
          <button class="btn btn-outline mt-3" :disabled="loading" @click="publishBatch.retry(editor)">{{ editor.state.publishFailure.operation === 'precheck' ? '重试该市场预检' : '重新准备该市场' }}</button>
        </div>
        <PublishPrecheckPanel
          :target="editor.target"
          :platform-options="platformOptions"
          :column-layout="targetEditors.length > 1"
          :precheck="editor.state.precheck"
          :payload-preview="editor.state.payloadPreview"
        />
        <div v-if="editor.state.busy" class="absolute inset-0 z-10 flex items-start justify-center rounded-lg bg-white/80 px-4 pt-12 backdrop-blur-sm dark:bg-dark-950/80" role="status">
          <div class="sticky top-4 rounded-lg border border-accent-200 bg-white p-4 text-center dark:border-dark-700 dark:bg-dark-900">
            <div class="mx-auto mb-3 size-6 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" />
            <p class="text-sm text-accent-700 dark:text-accent-200">{{ publishBatch.progress.message || '正在处理该市场的发布资料…' }}</p>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
