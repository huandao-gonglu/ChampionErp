<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, useId } from 'vue'
import { fetchOrderAddressNote, saveOrderAddressNote } from '@/api/orders'
import { useBackdropDismiss } from '@/composables/useBackdropDismiss'
import type { OrderAddressNote } from '@/types/orders'

const props = defineProps<{ orderId: string; shipmentId: string; address: string }>()
const emit = defineEmits<{ lock: [value: boolean] }>()
const bubbleId = useId()
const trigger = ref<HTMLButtonElement>()
const textarea = ref<HTMLTextAreaElement>()
const overlay = ref<HTMLElement>()
const opened = ref(false)
const loading = ref(false)
const saving = ref(false)
const current = ref<OrderAddressNote | null>(null)
const draft = ref('')
const error = ref('')
const latestNote = ref<string | null>(null)
const position = ref({ left: '16px', top: '16px', width: '360px' })
let request: AbortController | undefined
let disposed = false
function place() {
  const anchor = trigger.value?.getBoundingClientRect()
  if (!anchor) return
  const width = Math.min(380, window.innerWidth - 32)
  const height = Math.min(280, window.innerHeight - 32)
  position.value = {
    width: `${width}px`,
    left: `${Math.max(16, Math.min(anchor.left, window.innerWidth - width - 16))}px`,
    top: `${Math.max(16, Math.min(anchor.bottom + 8, window.innerHeight - height - 16))}px`,
  }
}
async function open() {
  if (opened.value) return
  opened.value = true
  loading.value = true
  current.value = null
  draft.value = error.value = ''
  latestNote.value = null
  emit('lock', true)
  place()
  window.addEventListener('resize', place)
  window.addEventListener('scroll', place, true)
  await nextTick()
  overlay.value?.focus()
  const pending = new AbortController()
  request = pending
  try {
    const value = await fetchOrderAddressNote(props.orderId, props.shipmentId, props.address, pending.signal)
    if (pending.signal.aborted || disposed) return
    current.value = value
    draft.value = value.note
  } catch (exc) {
    if (!pending.signal.aborted && !disposed) error.value = exc instanceof Error ? exc.message : '备注读取失败，请关闭后重试'
  } finally {
    if (!pending.signal.aborted && !disposed) {
      loading.value = false
      await nextTick()
      textarea.value?.focus()
    }
  }
}
function escape(event: KeyboardEvent) {
  if (event.isComposing) return
  event.preventDefault()
  event.stopPropagation()
  void close()
}
function dismiss() {
  request?.abort()
  opened.value = false
  emit('lock', false)
  resetBackdropPointer()
  window.removeEventListener('resize', place)
  window.removeEventListener('scroll', place, true)
  trigger.value?.focus()
}
async function close() {
  if (saving.value) return
  if (!current.value || draft.value === current.value.note) {
    dismiss()
    return
  }
  saving.value = true
  error.value = ''
  try {
    current.value = await saveOrderAddressNote({
      order_id: props.orderId, shipment_id: props.shipmentId,
      address_key: current.value.address_key, revision: current.value.revision, note: draft.value,
    })
    if (!disposed) dismiss()
  } catch (exc) {
    if (disposed) return
    error.value = `${exc instanceof Error ? exc.message : '备注保存失败'}。输入已保留，关闭气泡时重试。`
    // 保留草稿并展示冲突内容；用户核对后再次关闭才会覆盖最新版本。
    if ((exc as { code?: string }).code === 'ADDRESS_NOTE_CONFLICT') {
      try {
        const value = await fetchOrderAddressNote(props.orderId, props.shipmentId, props.address)
        if (!disposed) {
          current.value = value
          latestNote.value = value.note
        }
      } catch { /* 保留当前版本，避免读取失败后绕过冲突校验。 */ }
    }
  } finally {
    saving.value = false
    if (opened.value && !disposed) {
      await nextTick()
      textarea.value?.focus()
    }
  }
}
const { recordBackdropPointer, dismissFromBackdrop, resetBackdropPointer } = useBackdropDismiss(() => { void close() })
onBeforeUnmount(() => {
  disposed = true
  request?.abort()
  window.removeEventListener('resize', place)
  window.removeEventListener('scroll', place, true)
  emit('lock', false)
})
</script>

<template>
  <span class="address-note-control">
    <button ref="trigger" type="button" class="order-link" aria-haspopup="dialog" :aria-expanded="opened" :aria-controls="opened ? bubbleId : undefined" @click="open">备注</button>
    <div
      v-if="opened" ref="overlay" class="address-note-overlay" tabindex="-1"
      @pointerdown="recordBackdropPointer" @pointerup="dismissFromBackdrop" @pointercancel="resetBackdropPointer"
      @keydown.esc="escape" @keydown.tab.prevent="textarea?.focus()" @wheel.self.prevent
    >
      <div :id="bubbleId" role="dialog" aria-label="交货地址备注" aria-modal="true" :aria-busy="loading || saving" class="address-note-bubble" :style="position">
        <textarea
          ref="textarea" v-model="draft" aria-label="地址备注" maxlength="4000"
          :disabled="loading || saving || !current"
          :placeholder="loading ? '正在读取备注…' : '填写实际地址、收件人、电话或其他说明，关闭后自动保存'"
        />
        <p v-if="saving" role="status" class="order-muted">正在保存…</p>
        <p v-if="error" role="alert" class="order-error">{{ error }}</p>
        <p v-if="latestNote !== null" class="order-muted whitespace-pre-wrap">其他页面最新备注：{{ latestNote || '（空）' }}</p>
      </div>
    </div>
  </span>
</template>

<style scoped>
.address-note-control { display: inline-flex; }
.address-note-overlay { position: fixed; inset: 0; z-index: 60; outline: none; }
.address-note-bubble { position: fixed; box-sizing: border-box; padding: 10px; border: 1px solid var(--order-border); border-radius: 10px; background: var(--order-surface); box-shadow: 0 8px 28px rgb(0 0 0 / 22%); max-height: calc(100dvh - 32px); overflow-y: auto; }
textarea { display: block; box-sizing: border-box; width: 100%; height: min(220px, calc(100dvh - 80px)); resize: none; border: none; padding: 4px; background: transparent; color: var(--order-text); font: inherit; font-size: 13px; line-height: 1.65; outline: none; }
p { margin-top: 6px; font-size: 12px; }
</style>
