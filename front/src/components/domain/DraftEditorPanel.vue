<script setup lang="ts">
import { computed } from 'vue'
import type { DraftDetail, DraftProductContext } from '@/types/workflow'

const props = withDefaults(defineProps<{
  draft: DraftDetail
  productContext: DraftProductContext
  loading: boolean
  embedded?: boolean
}>(), {
  embedded: false,
})

const emit = defineEmits<{
  save: []
  generateCopy: []
  close: []
}>()

function listModel(getter: () => string[], setter: (value: string[]) => void) {
  return computed({
    get: () => getter().join('\n'),
    set: (value: string) => setter(value.split(/\n|,/).map((item) => item.trim()).filter(Boolean)),
  })
}

const bulletsText = listModel(() => props.draft.bullets, (value) => { props.draft.bullets = value })

const canGenerateCopy = computed(() => Boolean(props.draft.productId || props.productContext.productId))
</script>

<template>
  <section class="space-y-5">
    <div v-if="!props.embedded" class="rounded-lg border border-accent-200 bg-accent-50 p-4 dark:border-dark-700 dark:bg-dark-950/70">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <h2 class="text-xl font-black text-slate-950 dark:text-white">草稿编辑</h2>
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-outline" :disabled="props.loading || !canGenerateCopy" @click="emit('generateCopy')">生成/改写本地化文案</button>
          <button class="btn btn-primary" :disabled="props.loading || !props.draft.draftId" @click="emit('save')">保存草稿</button>
          <button class="btn btn-outline" @click="emit('close')">关闭</button>
        </div>
      </div>
    </div>

    <section class="space-y-4">
      <div class="flex items-center gap-2 text-sm">
        <span class="text-xs font-semibold text-slate-500 dark:text-accent-300">发布语言</span>
        <span class="badge-muted">{{ props.draft.language || '未设置' }}</span>
      </div>
      <label class="block">
        <span class="text-xs font-semibold text-slate-500">平台标题</span>
        <input v-model="props.draft.title" class="input mt-1" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-slate-500">商品描述</span>
        <textarea v-model="props.draft.description" class="input mt-1 min-h-36" />
      </label>
      <label class="block">
        <span class="text-xs font-semibold text-slate-500">商品卖点，每行一个</span>
        <textarea v-model="bulletsText" class="input mt-1 min-h-28" />
      </label>
    </section>
  </section>
</template>
