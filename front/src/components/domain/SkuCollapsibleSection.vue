<script setup lang="ts">
import { ref, useId } from 'vue'
import { PhCaretDown } from '@phosphor-icons/vue'

defineProps<{ title: string; summary: string }>()

const expanded = ref(true)
const contentId = useId()
</script>

<template>
  <section>
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h3 class="font-bold text-accent-950 dark:text-white">
        {{ title }} <span class="text-sm font-normal text-accent-500 dark:text-accent-400">{{ summary }}</span>
      </h3>
      <button
        type="button"
        class="btn btn-outline shrink-0"
        :aria-expanded="expanded"
        :aria-controls="contentId"
        @click="expanded = !expanded"
      >
        <PhCaretDown aria-hidden="true" :class="{ 'rotate-180': expanded }" />
        {{ expanded ? '收起 SKU' : '展开 SKU' }}
      </button>
    </div>
    <div v-show="expanded" :id="contentId" class="mt-3">
      <slot />
    </div>
  </section>
</template>
