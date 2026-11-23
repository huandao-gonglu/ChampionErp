import { computed, reactive, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import {
  checkDirectPurchasePaymentStatus,
  createDirectPurchase,
  fetchDirectPurchaseAddresses,
  fetchDirectPurchaseCashierUrl,
  fetchDirectPurchaseState,
  parseDirectPurchaseAddress,
  payDirectPurchase,
  previewDirectPurchase,
  reconcileDirectPurchase,
  resolveDirectPurchaseProduct,
  type DirectPurchaseAddressFields,
  type DirectPurchaseAddressPayload,
  type DirectPurchasePaymentAgreement,
  type DirectPurchaseRecord,
  type DirectPurchaseResolvedProduct,
  type DirectPurchaseState,
} from '@/api/alibabaDirectPurchase'
import type { BackendAlibabaPurchaseAddressCandidate } from '@/types/workflow.generated'

const PAID_STATUS_CODES = new Set([
  'waitsellersend',
  'waitbuyerreceive',
  'waitbuyerreceives',
  'confirm_goods',
  'success',
])

export const useAlibabaDirectPurchaseStore = defineStore('alibabaDirectPurchase', () => {
  const initialized = ref(false)
  const state = ref<DirectPurchaseState | null>(null)
  const stateLoading = ref(false)
  const stateError = ref('')

  const urlInput = ref('')
  const resolving = ref(false)
  const resolvedProduct = ref<DirectPurchaseResolvedProduct | null>(null)
  const selectedSkuIds = ref<string[]>([])
  const skuQuantities = reactive<Record<string, string | number>>({})
  const brokenImages = reactive<Record<string, true>>({})

  const addressMode = ref<'saved' | 'manual'>('saved')
  const savedAddresses = ref<BackendAlibabaPurchaseAddressCandidate[]>([])
  const selectedAddressId = ref('')
  const addressNotice = ref('')
  const addressLoading = ref(false)
  const addressError = ref('')
  const rawAddressText = ref('')
  const addressParsing = ref(false)
  const addressWarnings = ref<string[]>([])
  const manualAddressConfirmed = ref(false)
  const manualFields = reactive<DirectPurchaseAddressFields>({
    fullName: '',
    mobile: '',
    phone: '',
    provinceText: '',
    cityText: '',
    areaText: '',
    townText: '',
    address: '',
    postCode: '',
  })

  const activeRecord = ref<DirectPurchaseRecord | null>(null)
  const payChannel = ref<'alipay' | 'shegou' | ''>('')
  const createConfirmed = ref(false)
  const submissionUncertain = ref(false)
  const actionBusy = ref(false)
  const actionError = ref('')
  const actionNotice = ref('')
  const cashierUrls = reactive<Record<string, string>>({})
  const recordBusyId = ref('')

  const agreementLoading = ref(false)
  const agreement = ref<DirectPurchasePaymentAgreement | null>(null)
  const payConfirmed = ref(false)

  const canOperate = computed(() => Boolean(state.value?.session_ready))

  const purchasableSkus = computed(() =>
    (resolvedProduct.value?.skus || []).filter((sku) => sku.purchasable),
  )

  const selectedItems = computed(() => {
    if (!resolvedProduct.value) return []
    const byId = new Map(resolvedProduct.value.skus.map((sku) => [sku.sku_id, sku]))
    const list: Array<{ sku_id: string; quantity: number }> = []
    for (const skuId of selectedSkuIds.value) {
      const sku = byId.get(skuId)
      if (!sku || !sku.purchasable) continue
      const raw = String(skuQuantities[skuId] ?? '').trim()
      if (!/^\d+$/.test(raw)) continue
      const qty = Number(raw)
      if (!Number.isInteger(qty) || qty < 1 || qty > 10000) continue
      list.push({ sku_id: skuId, quantity: qty })
    }
    return list
  })

  const selectedTotalQuantity = computed(() =>
    selectedItems.value.reduce((sum, item) => sum + item.quantity, 0),
  )

  const selectedSavedAddress = computed(
    () => savedAddresses.value.find((item) => item.id === selectedAddressId.value) || null,
  )

  const manualAddressComplete = computed(() => {
    const requiredFilled = (['fullName', 'provinceText', 'cityText', 'areaText', 'address'] as const).every(
      (key) => manualFields[key].trim().length > 0,
    )
    const hasContact = manualFields.mobile.trim().length > 0 || manualFields.phone.trim().length > 0
    return requiredFilled && hasContact
  })

  const addressSelection = computed<DirectPurchaseAddressPayload | null>(() => {
    if (addressMode.value === 'saved') {
      if (selectedSavedAddress.value && !selectedSavedAddress.value.blocked_reason) {
        return { address_id: selectedSavedAddress.value.id }
      }
      return null
    }
    if (manualAddressComplete.value && manualAddressConfirmed.value) {
      return {
        address: {
          fullName: manualFields.fullName.trim(),
          mobile: manualFields.mobile.trim(),
          phone: manualFields.phone.trim(),
          provinceText: manualFields.provinceText.trim(),
          cityText: manualFields.cityText.trim(),
          areaText: manualFields.areaText.trim(),
          townText: manualFields.townText.trim(),
          address: manualFields.address.trim(),
          postCode: manualFields.postCode.trim(),
        },
        address_confirmed: true,
      }
    }
    return null
  })

  const canPreview = computed(
    () =>
      canOperate.value &&
      Boolean(resolvedProduct.value?.resolve_token) &&
      selectedItems.value.length > 0 &&
      selectedItems.value.length === selectedSkuIds.value.length &&
      Boolean(addressSelection.value) &&
      !actionBusy.value &&
      !resolving.value,
  )

  const canCreate = computed(
    () =>
      activeRecord.value?.state === 'preview' &&
      canOperate.value &&
      !submissionUncertain.value &&
      createConfirmed.value &&
      (payChannel.value === 'alipay' || payChannel.value === 'shegou') &&
      !actionBusy.value,
  )

  const activeOrderPaid = computed(() => {
    const rec = activeRecord.value
    if (!rec) return false
    return (
      PAID_STATUS_CODES.has(rec.order_status_code) ||
      rec.payment?.state === 'succeeded' ||
      rec.payment?.state === 'settled'
    )
  })

  const showProtocolPay = computed(() => {
    const rec = activeRecord.value
    if (!rec || rec.state !== 'created' || activeOrderPaid.value) return false
    if (rec.payment) return false
    return rec.pay_channel === 'alipay' || rec.pay_channel === 'shegou'
  })

  watch(
    manualFields,
    () => {
      manualAddressConfirmed.value = false
    },
    { deep: true },
  )

  watch(
    [selectedSkuIds, skuQuantities, addressSelection],
    () => {
      if (activeRecord.value?.state === 'preview' && !submissionUncertain.value) {
        activeRecord.value = null
        createConfirmed.value = false
      }
    },
    { deep: true },
  )

  watch(payChannel, () => {
    createConfirmed.value = false
  })

  function markImageBroken(url: string) {
    if (url) brokenImages[url] = true
  }

  function displayImage(url: string | undefined): string {
    const clean = String(url || '').trim()
    if (!clean || brokenImages[clean]) return ''
    return clean
  }

  function isSkuChecked(skuId: string): boolean {
    return selectedSkuIds.value.includes(skuId)
  }

  function toggleSku(skuId: string, checked: boolean) {
    if (checked) {
      if (!selectedSkuIds.value.includes(skuId)) {
        selectedSkuIds.value = [...selectedSkuIds.value, skuId]
      }
      const current = Number(skuQuantities[skuId])
      if (!Number.isInteger(current) || current < 1) {
        skuQuantities[skuId] = 1
      }
    } else {
      selectedSkuIds.value = selectedSkuIds.value.filter((id) => id !== skuId)
    }
  }

  function updateSkuQuantity(skuId: string, rawValue: string | number, purchasable: boolean) {
    skuQuantities[skuId] = rawValue
    const trimmed = String(rawValue ?? '').trim()
    if (purchasable && /^\d+$/.test(trimmed) && Number(trimmed) >= 1 && !selectedSkuIds.value.includes(skuId)) {
      selectedSkuIds.value = [...selectedSkuIds.value, skuId]
    }
  }

  function selectAllPurchasable() {
    const ids = purchasableSkus.value.map((sku) => sku.sku_id)
    for (const id of ids) {
      const current = Number(skuQuantities[id])
      if (!Number.isInteger(current) || current < 1) skuQuantities[id] = 1
    }
    selectedSkuIds.value = ids
  }

  function clearSkuSelection() {
    selectedSkuIds.value = []
  }

  function upsertRecord(record: DirectPurchaseRecord) {
    if (!state.value || record.state === 'preview') return
    const list = state.value.records.filter((row) => row.id !== record.id)
    state.value = { ...state.value, records: [record, ...list] }
  }

  async function loadAddresses(refresh = false) {
    if (addressLoading.value) return
    addressLoading.value = true
    addressError.value = ''
    try {
      const res = await fetchDirectPurchaseAddresses(refresh)
      savedAddresses.value = res.items
      addressNotice.value = res.notice
      if (!selectedAddressId.value) {
        const defaults = res.items.filter((item) => item.is_default && !item.blocked_reason)
        if (defaults.length === 1) {
          selectedAddressId.value = defaults[0]!.id
        } else {
          const firstUsable = res.items.find((item) => !item.blocked_reason)
          if (firstUsable) selectedAddressId.value = firstUsable.id
        }
      } else if (!res.items.some((item) => item.id === selectedAddressId.value)) {
        selectedAddressId.value = ''
      }
      if (res.items.length === 0 && res.notice) {
        addressMode.value = 'manual'
      }
    } catch (exc) {
      addressError.value = exc instanceof Error ? exc.message : '1688 地址读取失败，请切换手动输入'
    } finally {
      addressLoading.value = false
    }
  }

  async function loadState(loadAddressesToo = false) {
    stateLoading.value = true
    stateError.value = ''
    try {
      state.value = await fetchDirectPurchaseState()
      initialized.value = true
      if (activeRecord.value && activeRecord.value.state !== 'preview') {
        const updated = state.value.records.find((row) => row.id === activeRecord.value?.id)
        if (updated) activeRecord.value = updated
      }
      if (loadAddressesToo && state.value.authorized) {
        await loadAddresses()
      }
    } catch (exc) {
      stateError.value = exc instanceof Error ? exc.message : '直购状态读取失败，请重试'
    } finally {
      stateLoading.value = false
    }
  }

  async function ensureLoaded() {
    if (!initialized.value) {
      await loadState(true)
      return
    }
    await loadState(savedAddresses.value.length === 0)
  }

  async function handleParseAddress() {
    if (addressParsing.value || !rawAddressText.value.trim()) return
    addressParsing.value = true
    addressError.value = ''
    addressWarnings.value = []
    try {
      const res = await parseDirectPurchaseAddress(rawAddressText.value)
      Object.assign(manualFields, res.address)
      addressWarnings.value = res.warnings
      manualAddressConfirmed.value = false
    } catch (exc) {
      addressError.value = exc instanceof Error ? exc.message : '地址解析失败，请手动填写各项'
    } finally {
      addressParsing.value = false
    }
  }

  async function handleResolveProduct() {
    if (resolving.value || !urlInput.value.trim()) return
    resolving.value = true
    actionError.value = ''
    actionNotice.value = ''
    try {
      const result = await resolveDirectPurchaseProduct(urlInput.value.trim())
      for (const key of Object.keys(brokenImages)) delete brokenImages[key]
      resolvedProduct.value = result
      selectedSkuIds.value = []
      for (const key of Object.keys(skuQuantities)) delete skuQuantities[key]
      for (const sku of result.skus) {
        skuQuantities[sku.sku_id] = 1
      }
      const usable = result.skus.filter((sku) => sku.purchasable)
      if (usable.length === 1) {
        selectedSkuIds.value = [usable[0]!.sku_id]
      }
      if (activeRecord.value?.state === 'preview') {
        activeRecord.value = null
      }
      if (savedAddresses.value.length === 0 && state.value?.authorized) {
        void loadAddresses()
      }
    } catch (exc) {
      actionError.value = exc instanceof Error ? exc.message : '商品链接解析失败，请核对链接后重试'
    } finally {
      resolving.value = false
    }
  }

  async function handlePreview() {
    if (!canPreview.value || !resolvedProduct.value || !addressSelection.value) return
    actionBusy.value = true
    actionError.value = ''
    actionNotice.value = ''
    agreement.value = null
    payConfirmed.value = false
    try {
      const record = await previewDirectPurchase({
        resolve_token: resolvedProduct.value.resolve_token,
        items: selectedItems.value,
        ...addressSelection.value,
      })
      activeRecord.value = record
      const channels = record.preview.pay_channels
      payChannel.value = channels.includes('alipay')
        ? 'alipay'
        : channels.includes('shegou')
          ? 'shegou'
          : ''
      createConfirmed.value = false
      submissionUncertain.value = false
    } catch (exc) {
      actionError.value = exc instanceof Error ? exc.message : '订单预览失败，请检查起订量、库存及收货地址'
    } finally {
      actionBusy.value = false
    }
  }

  async function handleCreate() {
    if (!canCreate.value || !activeRecord.value || !payChannel.value) return
    const previewId = activeRecord.value.id
    const channel = payChannel.value
    actionBusy.value = true
    actionError.value = ''
    actionNotice.value = ''
    submissionUncertain.value = true
    try {
      const record = await createDirectPurchase(previewId, channel)
      activeRecord.value = record
      submissionUncertain.value = false
      createConfirmed.value = false
      agreement.value = null
      payConfirmed.value = false
      upsertRecord(record)
      if (record.state === 'created') {
        actionNotice.value = '1688 订单已创建，尚未自动扣款；可打开 1688 收银台付款或在手机 1688 App 完成支付。'
      }
    } catch (exc) {
      actionError.value = exc instanceof Error ? exc.message : '创建回执未确认，请点击「刷新状态」核对原单，勿重复下单'
    } finally {
      actionBusy.value = false
    }
  }

  async function handleReconcile(record: DirectPurchaseRecord) {
    if (actionBusy.value || recordBusyId.value) return
    recordBusyId.value = record.id
    actionError.value = ''
    actionNotice.value = ''
    try {
      const updated = await reconcileDirectPurchase(record.id)
      if (activeRecord.value?.id === updated.id) {
        activeRecord.value = updated
        submissionUncertain.value = false
      }
      upsertRecord(updated)
      if (updated.quota_credited) {
        actionNotice.value = '已同步 1688 付款状态，并已为当前万邦买家计入对应调用额度。'
      }
    } catch (exc) {
      actionError.value = exc instanceof Error ? exc.message : '刷新订单状态失败，请稍后重试'
    } finally {
      recordBusyId.value = ''
    }
  }

  async function handleCashier(record: DirectPurchaseRecord) {
    if (actionBusy.value || recordBusyId.value) return
    recordBusyId.value = record.id
    actionError.value = ''
    try {
      const url = await fetchDirectPurchaseCashierUrl(record.id)
      cashierUrls[record.id] = url
    } catch (exc) {
      actionError.value = exc instanceof Error ? exc.message : '获取 1688 收银台链接失败'
    } finally {
      recordBusyId.value = ''
    }
  }

  async function handleCheckAgreement(refresh = false) {
    const channel = activeRecord.value?.pay_channel
    if (agreementLoading.value || (channel !== 'alipay' && channel !== 'shegou')) return
    agreementLoading.value = true
    actionError.value = ''
    try {
      agreement.value = await checkDirectPurchasePaymentStatus(channel, refresh)
      payConfirmed.value = false
    } catch (exc) {
      actionError.value = exc instanceof Error ? exc.message : '免密签约状态查询失败'
    } finally {
      agreementLoading.value = false
    }
  }

  async function handleProtocolPay() {
    const rec = activeRecord.value
    if (!rec || actionBusy.value || !payConfirmed.value || !agreement.value?.ready) return
    actionBusy.value = true
    actionError.value = ''
    actionNotice.value = ''
    try {
      const updated = await payDirectPurchase(rec.id, rec.preview.total_fen)
      activeRecord.value = updated
      payConfirmed.value = false
      upsertRecord(updated)
      if (updated.quota_credited) {
        actionNotice.value = '免密支付成功，已同步计入当前万邦买家额度。'
      }
    } catch (exc) {
      actionError.value = exc instanceof Error ? exc.message : '免密支付失败，请刷新订单状态核对'
    } finally {
      actionBusy.value = false
    }
  }

  function inspectRecord(record: DirectPurchaseRecord) {
    activeRecord.value = record
    agreement.value = null
    payConfirmed.value = false
    actionError.value = ''
    actionNotice.value = ''
    submissionUncertain.value = false
  }

  return {
    initialized,
    state,
    stateLoading,
    stateError,
    urlInput,
    resolving,
    resolvedProduct,
    selectedSkuIds,
    skuQuantities,
    brokenImages,
    addressMode,
    savedAddresses,
    selectedAddressId,
    addressNotice,
    addressLoading,
    addressError,
    rawAddressText,
    addressParsing,
    addressWarnings,
    manualAddressConfirmed,
    manualFields,
    activeRecord,
    payChannel,
    createConfirmed,
    submissionUncertain,
    actionBusy,
    actionError,
    actionNotice,
    cashierUrls,
    recordBusyId,
    agreementLoading,
    agreement,
    payConfirmed,
    canOperate,
    purchasableSkus,
    selectedItems,
    selectedTotalQuantity,
    selectedSavedAddress,
    manualAddressComplete,
    addressSelection,
    canPreview,
    canCreate,
    activeOrderPaid,
    showProtocolPay,
    markImageBroken,
    displayImage,
    isSkuChecked,
    toggleSku,
    updateSkuQuantity,
    selectAllPurchasable,
    clearSkuSelection,
    loadState,
    ensureLoaded,
    loadAddresses,
    handleParseAddress,
    handleResolveProduct,
    handlePreview,
    handleCreate,
    handleReconcile,
    handleCashier,
    handleCheckAgreement,
    handleProtocolPay,
    inspectRecord,
  }
})
