<script setup lang="ts">
import { PhFloppyDisk } from '@phosphor-icons/vue'
import type { PageSaveAction } from '@/composables/usePageSaveAction'

const props = defineProps<{
  action: PageSaveAction
}>()

function handleClick(event: MouseEvent) {
  if (props.action.disabled) return
  if (props.action.form) {
    // 浏览器中由原生 form 属性触发表单校验与提交；未挂载到 document.body 的测试环境回退调用 requestSubmit。
    const button = event.currentTarget as HTMLButtonElement | null
    if (button && !button.form && props.action.submitForm) {
      props.action.submitForm()
    }
    return
  }
  props.action.onClick?.()
}
</script>

<template>
  <button
    :type="props.action.form ? 'submit' : 'button'"
    :form="props.action.form || undefined"
    :data-testid="props.action.testId || 'page-save-button'"
    :title="props.action.title || undefined"
    :disabled="props.action.disabled"
    class="btn btn-primary page-save-btn"
    @click="handleClick"
  >
    <PhFloppyDisk :size="15" aria-hidden="true" />{{ props.action.label }}
  </button>
</template>
