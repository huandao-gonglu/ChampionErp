<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { useBackdropDismiss } from '@/composables/useBackdropDismiss'

const props = defineProps<{ open: boolean; title: string }>()
const emit = defineEmits<{ close: [] }>()
const titleId = useId()
const dialog = ref<HTMLDialogElement | null>(null)
let restoreScroll = () => {}
const { recordBackdropPointer, dismissFromBackdrop, resetBackdropPointer } = useBackdropDismiss(() => emit('close'))

function unlock() {
  restoreScroll()
  restoreScroll = () => {}
}
watch(() => props.open, async (open) => {
  await nextTick()
  if (!dialog.value || props.open !== open) return
  if (open && !dialog.value.open) {
    const active = document.activeElement as HTMLElement | null
    // 外层草稿本身可滚动，连同页面一起锁定；关闭后还原原有样式和焦点。
    const locked: Array<[HTMLElement, string]> = []
    let node: HTMLElement | null = active || document.body
    while (node) {
      if (/(auto|scroll)/.test(getComputedStyle(node).overflowY) || node === document.body) {
        locked.push([node, node.style.overflow])
        node.style.overflow = 'hidden'
      }
      node = node.parentElement
    }
    restoreScroll = () => { locked.forEach(([element, overflow]) => { element.style.overflow = overflow }); if (active?.isConnected) active.focus({ preventScroll: true }) }
    dialog.value.showModal()
  } else if (!open) {
    dialog.value.close()
    resetBackdropPointer()
    unlock()
  }
}, { immediate: true })
onBeforeUnmount(() => { dialog.value?.close(); unlock() })
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog" :aria-labelledby="titleId" aria-modal="true"
      class="fixed inset-0 m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-4xl rounded-2xl border border-slate-200 bg-white p-0 text-slate-900 shadow-2xl backdrop:bg-slate-950/70 open:flex open:flex-col dark:border-dark-700 dark:bg-dark-900 dark:text-white"
      @cancel.prevent="emit('close')" @pointerdown="recordBackdropPointer" @pointerup="dismissFromBackdrop" @pointercancel="resetBackdropPointer"
    >
      <header class="flex shrink-0 items-center justify-between gap-4 border-b border-slate-200 px-5 py-4 dark:border-dark-700">
        <h3 :id="titleId" class="text-lg font-bold">{{ title }}</h3>
        <button type="button" class="btn btn-outline" :aria-label="`关闭${title}`" autofocus @click="emit('close')">关闭</button>
      </header>
      <div class="min-h-0 overflow-y-auto overscroll-contain p-5"><slot /></div>
    </dialog>
  </Teleport>
</template>
