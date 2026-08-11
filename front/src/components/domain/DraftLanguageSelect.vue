<script setup lang="ts">
import { computed } from 'vue'
import type { MarketplaceOption } from '@/types/workflow'
import { draftLanguageKey, draftLanguageOptions, draftTargetsForLanguage } from '@/utils/draftTargetOptions'

const props = withDefaults(defineProps<{
  language: string
  platformOptions: MarketplaceOption[]
  loading: boolean
  compact?: boolean
}>(), {
  compact: false,
})

const emit = defineEmits<{
  updateLanguage: [language: string]
}>()

const options = computed(() => draftLanguageOptions(props.platformOptions))
const targetCount = computed(() => draftTargetsForLanguage(props.platformOptions, props.language).length)

function changeLanguage(event: Event) {
  const language = (event.target as HTMLSelectElement).value
  if (!language || draftLanguageKey(language) === draftLanguageKey(props.language)) return
  emit('updateLanguage', language)
}
</script>

<template>
  <select
    data-testid="draft-language-select"
    class="input w-full min-w-0"
    :class="props.compact ? 'h-8 px-2 py-1 text-xs' : ''"
    :value="props.language"
    :title="`可选市场 ${targetCount} 个`"
    :disabled="props.loading || !options.length"
    @change="changeLanguage"
  >
    <option value="" disabled>选择语言</option>
    <option v-for="option in options" :key="option.value" :value="option.value">{{ option.value }}</option>
  </select>
</template>
