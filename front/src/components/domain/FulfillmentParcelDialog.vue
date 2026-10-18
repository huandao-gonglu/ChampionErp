<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { PhPlus, PhDotsThree } from '@phosphor-icons/vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import { fetchFulfillment, fulfillmentCommand } from '@/api/fulfillment'
import type { FulfillmentDetail, DomesticParcel } from '@/types/fulfillment'
import type { OrderDetail } from '@/types/orders'
import { domesticWaybillKey, parcelError } from './purchaseLogistics'
const props = defineProps<{ value: FulfillmentDetail; order: OrderDetail; focusParcelId?: string }>()
const emit = defineEmits<{ close: []; saved: [FulfillmentDetail] }>()
const trackingInputs = ref<HTMLInputElement[]>([])
const expanded = reactive(new Set<string>())
const parcels = ref<DomesticParcel[]>(props.value.parcels.map(p => ({ ...p })))
const includedCount = computed(() => parcels.value.filter(parcel => parcel.included).length)
const unchanged = computed(() => JSON.stringify(parcels.value) === JSON.stringify(props.value.parcels))
const persisted = new Set(props.value.parcels.map(p => p.id))
const busy = ref(false)
const error = ref('')
const revision = ref(props.value.revision)
const locked = ref(!props.value.editable)
function add() {
  parcels.value.push({ id: crypto.randomUUID(), carrier: '', tracking_number: '', quantity: null,
    description: '', image_url: '', source_platform: '', purchase_order_number: '', included: true })
}
function remove(parcel: DomesticParcel) {
  if (persisted.has(parcel.id)) parcel.included = false
  else parcels.value = parcels.value.filter(p => p.id !== parcel.id)
}
function useProduct(parcel: DomesticParcel, event: Event) {
  const key = (event.target as HTMLSelectElement).value
  const line = props.order.lines.find(l => l.selection.line_key === key)
  if (line) {
    parcel.description = [parcel.description, `${line.line.title} · ${line.line.sku}`].filter(Boolean).join('\n')
    if (!parcel.image_url) parcel.image_url = line.line.image_url || ''
  }
  ;(event.target as HTMLSelectElement).value = ''
}
function setQuantity(parcel: DomesticParcel, event: Event) {
  const input = (event.target as HTMLInputElement).value
  parcel.quantity = input === '' ? null : Number(input)
}
async function save(confirmed: boolean) {
  if (busy.value || locked.value) return
  error.value = parcelError(parcels.value, confirmed)
  if (error.value) return
  busy.value = true
  try {
    emit('saved', await fulfillmentCommand('parcels', props.value.erp_order_id, revision.value, {
      parcels: parcels.value.map(p => ({ ...p, tracking_number: domesticWaybillKey(p.tracking_number) })),
      confirm_parcels: confirmed,
    }))
  } catch (exc) {
    const message = exc instanceof Error ? exc.message : '包裹保存失败'
    error.value = message
    if (message.startsWith('履约资料已更新')) {
      try {
        const latest = await fetchFulfillment(props.value.erp_order_id)
        revision.value = latest.revision
        locked.value = !latest.editable
        error.value = `${message}。输入已保留，请核对最新包裹后重新打开编辑。`
        locked.value = true
      } catch { error.value = message }
    }
  } finally { busy.value = false }
}
if (!parcels.value.length) add()
onMounted(async () => {
  await nextTick()
  const index = parcels.value.findIndex(parcel => parcel.id === props.focusParcelId)
  if (index >= 0) trackingInputs.value[index]?.focus()
})
</script>
<template>
  <WorkspaceDialog
    :open="true"
    title="管理国内包裹"
    width="1040px"
    class="order-ui parcel-editor-dialog"
    :close-disabled="busy"
    @close="emit('close')"
  >
    <div class="order-row parcel-editor-summary">
      <p>
        本订单 {{ parcels.length }} 个包裹 · 本次已选 {{ includedCount }} 个 · <span :class="value.parcels_confirmed && unchanged ? 'list-confirmed' : 'list-unconfirmed'">
          {{ value.parcels_confirmed && unchanged ? '清单已确认' : '清单待确认' }}
        </span>
      </p><button
        type="button"
        class="order-button"
        :disabled="busy || locked"
        @click="add"
      >
        <PhPlus :size="18" aria-hidden="true" />新增包裹
      </button>
    </div>
    <p class="order-muted mt-2">
      订单 {{ order.order.order_id }} · 登记实际运单；补寄可直接新增包裹。
    </p>
    <p v-if="error" class="order-error mt-3" role="alert">
      {{ error }}
    </p>
    <fieldset :disabled="busy || locked" class="parcel-editor-fields">
      <div class="parcel-editor-table-wrap">
        <table class="parcel-editor-table">
          <thead><tr><th>本次报单</th><th>国内运单号</th><th>物流公司（选填）</th><th>包裹件数</th><th>内容说明（选填）</th><th>操作</th></tr></thead><tbody>
            <template v-for="(parcel, index) in parcels" :key="parcel.id">
              <tr :data-included="parcel.included">
                <td><input v-model="parcel.included" type="checkbox" :aria-label="`包裹 ${index + 1} 参与本次报单`" /></td><td>
                  <input
                    ref="trackingInputs"
                    v-model="parcel.tracking_number"
                    class="order-input"
                    aria-label="国内快递单号"
                    maxlength="100"
                  />
                </td><td>
                  <input
                    v-model="parcel.carrier"
                    class="order-input"
                    aria-label="快递公司"
                    maxlength="80"
                  />
                </td><td>
                  <input
                    :value="parcel.quantity"
                    class="order-input"
                    aria-label="商品件数"
                    type="number"
                    min="1"
                    step="1"
                    placeholder="待完善"
                    @input="setQuantity(parcel, $event)"
                  />
                </td><td>
                  <textarea
                    v-model="parcel.description"
                    class="order-input"
                    aria-label="货物说明"
                    maxlength="10000"
                    rows="1"
                    placeholder="填写包裹内容"
                  />
                </td><td>
                  <button
                    type="button"
                    class="order-link parcel-extra-toggle"
                    :aria-label="`包裹 ${index + 1} 更多资料`"
                    :aria-expanded="expanded.has(parcel.id)"
                    @click="expanded.has(parcel.id) ? expanded.delete(parcel.id) : expanded.add(parcel.id)"
                  >
                    <PhDotsThree :size="22" aria-hidden="true" />
                  </button>
                </td>
              </tr>
              <tr v-if="expanded.has(parcel.id)" class="parcel-extra-row">
                <td colspan="6">
                  <div class="order-form order-form-columns">
                    <label>从订单商品带入说明和图片<select class="order-input" aria-label="带入商品资料" @change="useProduct(parcel, $event)">
                      <option value="">
                        选择商品（选填）
                      </option><option v-for="line in order.lines" :key="line.selection.line_key" :value="line.selection.line_key">
                        {{ line.line.title }} · {{ line.line.sku }}
                      </option>
                    </select></label><label>商品图片地址<input v-model="parcel.image_url" class="order-input" maxlength="4096" /></label><label>采购平台<select v-model="parcel.source_platform" class="order-input">
                      <option value="">
                        未填写
                      </option><option value="1688">
                        1688
                      </option><option value="taobao">
                        淘宝
                      </option><option value="pinduoduo">
                        拼多多
                      </option>
                    </select></label><label>采购单号<input v-model="parcel.purchase_order_number" class="order-input" maxlength="200" /></label>
                  </div><div class="order-actions mt-3">
                    <button
                      v-if="parcel.included"
                      type="button"
                      class="order-link"
                      @click="remove(parcel)"
                    >
                      {{ persisted.has(parcel.id) ? '移出本次报单' : '删除' }}
                    </button><button
                      v-else
                      type="button"
                      class="order-link"
                      @click="parcel.included = true"
                    >
                      加入本次报单
                    </button>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </fieldset>
    <p class="order-muted mt-4">
      勾选参与本次报单的包裹。保存包裹仅更新资料；保存并确认清单后，参与包裹将按本单方案提交或更新。
    </p>
    <template #footer>
      <div class="order-footer">
        <button class="order-button" :disabled="busy" @click="emit('close')">
          取消
        </button><button class="order-button" :disabled="busy || locked" @click="save(false)">
          保存包裹
        </button><button class="order-button order-primary" :disabled="busy || locked" @click="save(true)">
          {{ busy ? '保存中…' : '保存并确认清单' }}
        </button>
      </div>
    </template>
  </WorkspaceDialog>
</template>
<style scoped>
.parcel-editor-summary { flex-wrap: wrap; gap: 12px; }
.list-confirmed { color: var(--order-positive); }
.list-unconfirmed { color: var(--order-warning); }
.parcel-editor-fields { min-width: 0; margin-top: 20px; }
.parcel-editor-table-wrap { overflow-x: auto; border: 1px solid var(--order-border); border-radius: 6px; }
.parcel-editor-table { width: 100%; min-width: 780px; border-collapse: collapse; font-size: 14px; }
th { background: var(--order-soft); text-align: left; color: var(--order-muted); font-weight: 500; white-space: nowrap; }
th, td { padding: 12px 10px; border-bottom: 1px solid var(--order-border); }
tr:last-child td { border-bottom: 0; }
th:nth-child(1) { width: 80px; }
th:nth-child(2) { width: 28%; }
th:nth-child(3) { width: 17%; }
th:nth-child(4) { width: 100px; }
th:last-child { width: 48px; }
input[type='checkbox'] { width: 18px; height: 18px; accent-color: #16a34a; }
textarea.order-input { resize: vertical; min-height: 40px; }
.parcel-extra-row td { background: var(--order-soft); padding: 16px; }
.parcel-extra-toggle { padding: 4px 0; }
</style>
