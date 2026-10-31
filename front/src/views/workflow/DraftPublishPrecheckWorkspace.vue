<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import PublishPreparationProgress from '@/components/domain/PublishPreparationProgress.vue'
import DraftBarcodeFields from '@/components/domain/DraftBarcodeFields.vue'
import { useWorkflowCatalogStore } from '@/stores/workflow/catalog'
import PublishPrecheckPanel from '@/components/domain/PublishPrecheckPanel.vue'
import DraftWarrantyFields from '@/components/domain/DraftWarrantyFields.vue'
import { useWorkflowStore } from '@/stores/workflow'
import { useWorkflowActivityStore } from '@/stores/workflow/activity'
import { useWorkflowPublishingStore } from '@/stores/workflow/publishing'

const { targetEditors, publishBatch } = storeToRefs(useWorkflowStore())
const { platformOptions } = storeToRefs(useWorkflowPublishingStore())
const { currentDraftProductContext } = storeToRefs(useWorkflowCatalogStore())
const { loading } = storeToRefs(useWorkflowActivityStore())
const readyNames = computed(() => publishBatch.value.ready.map((editor) => {
  const platform = platformOptions.value.find((item) => item.key === editor.target.platform)?.label || editor.target.platform
  return `${platform} · ${editor.target.site}`
}).join('、'))

</script>

<template>
  <div class="space-y-5">
    <section class="rounded-lg border border-accent-200 bg-white p-5 dark:border-dark-700 dark:bg-dark-900/80" data-testid="publish-batch-actions">
      <h2 class="card-title">发布预检</h2>
      <p class="muted mt-1">基础资料预检 → 准备素材与预览 → 最终发布校验 → 确认发布。基础预检检查图片来源；准备时上传图片并自动执行最终校验，通过后再确认加入队列。</p>
      <div class="mt-4 flex flex-wrap gap-3">
        <button class="btn btn-outline" :disabled="loading || !publishBatch.pending.length" @click="publishBatch.precheckAll">基础资料预检</button>
        <button class="btn btn-outline" :disabled="loading || !publishBatch.preparable.length" @click="publishBatch.previewAll">准备素材与发布预览（{{ publishBatch.preparable.length }}）</button>
        <button class="btn btn-primary" :disabled="loading || !publishBatch.ready.length" @click="publishBatch.enqueueAll">确认发布已就绪市场（{{ publishBatch.ready.length }}）</button>
      </div>
      <p class="muted mt-3">共 {{ targetEditors.length }} 个目标 · 待处理 {{ publishBatch.pending.length }} 个 · 基础预检通过 {{ publishBatch.passed.length }} 个 · 最终校验通过 {{ publishBatch.ready.length }} 个 · 已入队 {{ publishBatch.queuedCount }} 个</p>
      <p v-if="readyNames" class="mt-2 text-sm text-accent-700 dark:text-accent-200">本次确认将提交：{{ readyNames }}。请核对下方各市场的发布摘要。</p>
      <p v-if="publishBatch.progress.message" class="muted mt-2" role="status">{{ publishBatch.progress.message }}<span v-if="publishBatch.progress.operation">（{{ publishBatch.progress.completed }} / {{ publishBatch.progress.total }}）</span></p>
      <p v-if="publishBatch.progress.error" class="mt-2 text-sm text-rose-700 dark:text-rose-300" role="alert">{{ publishBatch.progress.error }}</p>
    </section>
    <div class="grid items-start gap-6" :class="targetEditors.length > 1 ? 'xl:grid-cols-2' : ''">
      <section
        v-for="editor in targetEditors"
        :key="editor.key"
        :aria-label="`${editor.target.platform} 发布预检`"
        class="relative min-w-0"
        data-testid="publish-target-editor"
      >
        <DraftWarrantyFields v-if="['mercadolibre', 'yandex'].includes(editor.target.platform) && !editor.state.queuedPublishJobId" :draft="editor.draft" :issues="editor.state.precheck?.errorItems || []" :loading="loading" @invalidate-publish-validation="editor.actions.invalidatePublishValidation" />
        <DraftBarcodeFields v-if="!editor.state.queuedPublishJobId" :draft="editor.draft" :target="editor.target" :category="editor.state.category" :skus="currentDraftProductContext.skuItems" :loading="loading" auto-load @change="editor.actions.invalidatePublishValidation" />
        <p v-if="editor.state.queuedPublishJobId" class="mb-3 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-200" role="status">已加入任务队列 · {{ editor.state.queuedPublishJobId }}</p>
        <div v-else-if="editor.state.publishFailure" class="mb-3 rounded-lg bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300" role="alert">
          <p class="whitespace-pre-line break-words">{{ editor.state.publishFailure.message }}</p>
          <button class="btn btn-outline mt-3" :disabled="loading" @click="publishBatch.retry(editor)">{{ editor.state.publishFailure.operation === 'precheck' ? '重试该市场预检' : '重新准备该市场' }}</button>
        </div>
        <PublishPreparationProgress v-if="!editor.state.busy && editor.state.preparationProgress" class="mb-3 rounded-lg border border-accent-200 p-3 dark:border-dark-700" :progress="editor.state.preparationProgress" />
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
            <PublishPreparationProgress v-if="editor.state.preparationProgress" :progress="editor.state.preparationProgress" />
            <p v-else class="text-sm text-accent-700 dark:text-accent-200">{{ publishBatch.progress.message || '正在处理该市场的发布资料…' }}</p>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
