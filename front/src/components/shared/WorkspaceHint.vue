<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { PhInfo } from '@phosphor-icons/vue'

defineProps<{ content: string; label: string }>()
const id = useId()
const anchor = ref<HTMLElement>()
const panel = ref<HTMLElement>()
const teleportTarget = ref<HTMLElement | string>('body')
const open = ref(false)
const positioned = ref(false)
const left = ref(0)
const top = ref(0)
const width = ref(320)
const maxHeight = ref(320)
let pinned = false
let timer: ReturnType<typeof setTimeout> | undefined

function keep() { clearTimeout(timer) }
function hide() {
  keep()
  pinned = false
  open.value = false
}
async function show() {
  keep()
  if (!anchor.value) return
  // 原生模态框位于顶层，提示必须挂在框内，才能显示在遮罩上方。
  teleportTarget.value = anchor.value.closest('dialog') || document.body
  positioned.value = false
  open.value = true
  await nextTick()
  if (!open.value || !anchor.value) return
  const rect = anchor.value.getBoundingClientRect()
  width.value = Math.min(320, window.innerWidth - 24)
  left.value = Math.max(12, Math.min(rect.left, window.innerWidth - width.value - 12))
  const below = window.innerHeight - rect.bottom - 20
  const above = rect.top - 20
  maxHeight.value = Math.max(40, Math.min(320, Math.max(below, above)))
  await nextTick()
  if (!open.value) return
  const height = Math.min(panel.value?.offsetHeight || 120, maxHeight.value)
  top.value = below >= height ? rect.bottom + 6 : Math.max(12, rect.top - height - 6)
  positioned.value = true
}
function leave() {
  keep()
  if (!pinned) timer = setTimeout(() => {
    if (!anchor.value?.contains(document.activeElement)) hide()
  }, 120)
}
function toggle() {
  if (pinned) hide()
  else { pinned = true; void show() }
}
function outside(event: PointerEvent) {
  if (event.target instanceof Node && !anchor.value?.contains(event.target) && !panel.value?.contains(event.target)) hide()
}
function escape(event: KeyboardEvent) {
  if (!open.value || event.key !== 'Escape') return
  event.preventDefault()
  event.stopPropagation()
  hide()
}
function viewportChanged(event: Event) {
  if (event.target instanceof Node && panel.value?.contains(event.target)) return
  hide()
}
onMounted(() => {
  document.addEventListener('pointerdown', outside)
  document.addEventListener('keydown', escape, true)
  window.addEventListener('scroll', viewportChanged, true)
  window.addEventListener('resize', viewportChanged)
})
onBeforeUnmount(() => {
  keep()
  document.removeEventListener('pointerdown', outside)
  document.removeEventListener('keydown', escape, true)
  window.removeEventListener('scroll', viewportChanged, true)
  window.removeEventListener('resize', viewportChanged)
})
</script>

<template>
  <span ref="anchor" class="inline-flex shrink-0 align-middle" @mouseenter="show" @mouseleave="leave">
    <slot name="trigger" :attrs="{ 'aria-describedby': open ? id : undefined, onFocus: show, onBlur: leave }">
      <button
        type="button"
        class="inline-flex size-5 cursor-pointer items-center justify-center rounded text-accent-400 hover:text-primary-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500 dark:text-accent-400 dark:hover:text-primary-300"
        :aria-label="label"
        :aria-describedby="open ? id : undefined"
        :aria-expanded="open"
        @focus="show"
        @blur="leave"
        @click.prevent.stop="toggle"
      ><PhInfo :size="15" aria-hidden="true" /></button>
    </slot>
    <Teleport :to="teleportTarget">
      <span
        v-if="open"
        :id="id"
        ref="panel"
        role="tooltip"
        class="workspace-hint-panel"
        :style="{ left: `${left}px`, top: `${top}px`, width: `${width}px`, maxHeight: `${maxHeight}px`, visibility: positioned ? 'visible' : 'hidden' }"
        @mouseenter="keep"
        @mouseleave="leave"
      >{{ content }}</span>
    </Teleport>
  </span>
</template>

<style scoped>
.workspace-hint-panel {
  @apply fixed z-[1100] box-border overflow-y-auto whitespace-pre-line break-words rounded-lg border border-accent-200 bg-white p-3 text-left text-xs font-normal leading-relaxed text-accent-700 shadow-lg dark:border-dark-700 dark:bg-dark-800 dark:text-accent-200;
}
</style>
