import { ref } from 'vue'
import { defineStore } from 'pinia'
import { createEmptyDraftDetail, createEmptyDraftProductContext, createEmptyProduct } from '@/constants/initialState'
import type { DraftDetail, DraftIndexItem, DraftProductContext, Product, ProductIndexItem } from '@/types/workflow'

export const useWorkflowCatalogStore = defineStore('workflow-catalog', () => {
  const product = ref<Product>(createEmptyProduct())
  const productsIndex = ref<ProductIndexItem[]>([])
  const draftsIndex = ref<DraftIndexItem[]>([])
  const selectedProductIds = ref<string[]>([])
  const currentDraft = ref<DraftDetail>(createEmptyDraftDetail())
  const currentDraftProductContext = ref<DraftProductContext>(createEmptyDraftProductContext())
  const imagePrompt = ref('')

  return {
    product,
    productsIndex,
    draftsIndex,
    selectedProductIds,
    currentDraft,
    currentDraftProductContext,
    imagePrompt,
  }
})
