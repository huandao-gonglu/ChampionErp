<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import type { OrderProgressHintContent } from './orderProgressHint'
const props = defineProps<{ content: OrderProgressHintContent }>()
const id = useId()
const anchor = ref<HTMLElement>()
const panel = ref<HTMLElement>()
const open = ref(false)
const left = ref(0)
const top = ref(0)
const width = ref(360)
const maxHeight = ref(320)
let timer: ReturnType<typeof setTimeout> | undefined
const visible = computed(() => open.value && props.content.sections.length > 0)
function keep() { clearTimeout(timer) }
function hide() { keep(); open.value = false }
async function show() {
  keep()
  if (!props.content.sections.length || !anchor.value) return
  open.value = true
  await nextTick()
  if (!open.value || !anchor.value) return
  const rect = anchor.value.getBoundingClientRect()
  width.value = Math.min(360, window.innerWidth - 24)
  left.value = Math.max(12, Math.min(rect.left, window.innerWidth - width.value - 12))
  const below = window.innerHeight - rect.bottom - 20
  maxHeight.value = Math.max(80, Math.min(320, Math.max(below, rect.top - 20)))
  top.value = below >= Math.min(panel.value?.offsetHeight || 160, 320) ? rect.bottom + 6 : Math.max(12, rect.top - Math.min(panel.value?.offsetHeight || 160, maxHeight.value) - 6)
}
function leave() { keep(); timer = setTimeout(hide, 120) }
function viewportChanged(event: Event) {
  if (event.target instanceof Node && panel.value?.contains(event.target)) return
  hide()
}
onMounted(() => { window.addEventListener('scroll', viewportChanged, true); window.addEventListener('resize', viewportChanged) })
onBeforeUnmount(() => { keep(); window.removeEventListener('scroll', viewportChanged, true); window.removeEventListener('resize', viewportChanged) })
</script>
<template>
  <span ref="anchor" class="progress-hint-anchor" @mouseenter="show" @mouseleave="leave" @focusin="show" @focusout="leave" @keydown.esc="hide">
    <slot :described-by="visible ? id : undefined" />
    <Teleport to="body">
      <section v-if="visible" :id="id" ref="panel" role="tooltip" class="order-progress-hint order-ui" :style="{ left: `${left}px`, top: `${top}px`, width: `${width}px`, maxHeight: `${maxHeight}px` }" @mouseenter="keep" @mouseleave="leave">
        <strong>{{ content.title }}</strong>
        <article v-for="(section, index) in content.sections" :key="index">
          <p><strong>{{ section.heading }}</strong></p>
          <p v-for="(line, lineIndex) in section.lines" :key="lineIndex" class="progress-muted">{{ line }}</p>
          <p v-if="section.warning" class="progress-warning">{{ section.warning }}</p>
        </article>
      </section>
    </Teleport>
  </span>
</template>
<style scoped>
.progress-hint-anchor { display: inline-flex; }
.order-progress-hint { position: fixed; z-index: 1000; box-sizing: border-box; padding: 12px; border: 1px solid var(--order-border); border-radius: 8px; background: var(--order-surface); color: var(--order-text); box-shadow: 0 8px 24px rgb(0 0 0 / 16%); overflow-y: auto; font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
article { margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--order-border); }
p { margin: 2px 0; }
.progress-muted { color: var(--order-muted); }
.progress-warning { color: var(--color-danger, #dc2626); }
</style>
