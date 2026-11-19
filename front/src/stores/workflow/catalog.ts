import { ref, shallowRef, watch } from 'vue'
import { defineStore } from 'pinia'
import { createEmptyDraftDetail, createEmptyDraftProductContext, createEmptyProduct } from '@/constants/initialState'
import type { DraftDetail, DraftIndexItem, DraftProductContext, Product, ProductIndexItem } from '@/types/workflow'

export const useWorkflowCatalogStore = defineStore('workflow-catalog', () => {
  const product = ref<Product>(createEmptyProduct())
  const productsIndex = ref<ProductIndexItem[]>([])
  const libraryRows = shallowRef<ProductIndexItem[]>(productsIndex.value)
  watch(productsIndex, rows => { libraryRows.value = rows }, { flush: 'sync' })
  const draftsIndex = ref<DraftIndexItem[]>([])
  const selectedProductIds = ref<string[]>([])
  const currentDraft = ref<DraftDetail>(createEmptyDraftDetail())
  const currentDraftProductContext = ref<DraftProductContext>(createEmptyDraftProductContext())
  const imagePrompt = ref('')

  return {
    product,
    productsIndex,
    libraryRows,
    draftsIndex,
    selectedProductIds,
    currentDraft,
    currentDraftProductContext,
    imagePrompt,
  }
})
